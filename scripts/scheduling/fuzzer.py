#!/usr/bin/env python3
"""Fuzzer job - selects least-fuzzed commit from fuzzing schedule"""

import os
import sys
from typing import Optional

scripts_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, scripts_dir)

from scheduling.s3_state import get_state_manager, S3StateError


def get_least_fuzzed_commit(solver: str) -> Optional[str]:
    """Get the least-fuzzed commit from fuzzing schedule v2. Returns None if schedule is empty.
    When all commits have the same fuzz_count, returns the oldest (first in list).
    
    NOTE: This is a read-only operation. For atomic selection with increment, use select_and_increment_least_fuzzed_v2."""
    manager = get_state_manager(solver)
    schedule = manager.get_fuzzing_schedule_v2()
    
    if not schedule:
        return None
    
    # Find minimum fuzz_count
    min_fuzz_count = min(c.get('fuzz_count', 0) for c in schedule)
    
    # Find first commit with minimum fuzz_count (oldest among least-fuzzed)
    for commit_info in schedule:
        if commit_info.get('fuzz_count', 0) == min_fuzz_count:
            return commit_info.get('hash')
    
    # Fallback (shouldn't happen)
    return schedule[0].get('hash')


def increment_fuzz_count_and_manage(solver: str, commit_hash: str) -> None:
    """Manage schedule size after fuzzing completes.
    If schedule > 4 and all commits are unfuzzed, remove the commit we just fuzzed.
    
    NOTE: fuzz_count is now incremented atomically during selection (in run_fuzzer),
    so we don't increment it again here. This function only handles schedule management."""
    manager = get_state_manager(solver)
    
    # Verify commit exists in schedule
    schedule = manager.get_fuzzing_schedule_v2()
    commit_found = any(c.get('hash') == commit_hash for c in schedule)
    
    if not commit_found:
        print(f"⚠️  Commit {commit_hash[:8]} not found in schedule (may have been removed)", file=sys.stderr)
        return
    
    # Get current fuzz_count for logging
    current_fuzz_count = None
    for commit in schedule:
        if commit.get('hash') == commit_hash:
            current_fuzz_count = commit.get('fuzz_count', 0)
            break
    
    print(f"✅ Fuzzing completed for {commit_hash[:8]} (fuzz_count: {current_fuzz_count})")
    
    # Check schedule size and manage if needed
    schedule_size = len(schedule)
    
    if schedule_size > 4:
        # Check if all commits are unfuzzed (fuzz_count <= 1)
        all_unfuzzed = all(c.get('fuzz_count', 0) <= 1 for c in schedule)
        
        if all_unfuzzed:
            print(f"📋 Schedule has {schedule_size} commits, all unfuzzed. Removing oldest: {commit_hash[:8]}")
            manager.remove_from_fuzzing_schedule_v2(commit_hash)
            print(f"✅ Removed {commit_hash[:8]} from schedule")


from typing import Tuple
def run_fuzzer(solver: str, verify_binary: bool = True) -> Tuple[Optional[str], Optional[str]]:
    """Get commit to fuzz from schedule v2 and latest build to use.
    Returns (commit_to_fuzz, latest_build_to_use) tuple.
    
    - commit_to_fuzz: Oldest commit from fuzzing schedule v2 (FIFO)
    - latest_build_to_use: Latest available build from S3 (to use for fuzzing)
    
    This allows fuzzing old commits using the latest build to avoid discovering
    bugs that were already fixed in newer commits."""
    try:
        manager = get_state_manager(solver)
        
        # Step 1: Get commit to fuzz (oldest from schedule v2, FIFO)
        commit_to_fuzz = manager.select_and_increment_least_fuzzed_v2()
        if not commit_to_fuzz:
            print("⏭️  No commits in fuzzing schedule v2", file=sys.stderr)
            return None, None
        
        # Step 2: Get latest available build from S3
        latest_build = manager.get_latest_available_build()
        if not latest_build:
            print("⏭️  No builds available in S3", file=sys.stderr)
            return None, None
        
        if verify_binary:
            from botocore.exceptions import ClientError
            
            s3_key = f"solvers/{solver}/builds/production/{latest_build}.tar.gz"
            
            try:
                manager.s3_client.head_object(Bucket=manager.bucket, Key=s3_key)
            except ClientError as e:
                if e.response.get('Error', {}).get('Code') == '404':
                    print(f"⚠️  Binary not found for latest build {latest_build[:8]}, skipping", file=sys.stderr)
                    return None, None
                raise S3StateError(f"Error checking binary existence: {e}")
        
        print(f"✅ Selected commit {commit_to_fuzz[:8]} to fuzz using latest build {latest_build[:8]}", file=sys.stderr)
        return commit_to_fuzz, latest_build
    except S3StateError as e:
        print(f"❌ S3 State Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Fuzzer job - select commit from fuzzing schedule')
    parser.add_argument('solver', choices=['z3', 'cvc5'], help='Solver name')
    subparsers = parser.add_subparsers(dest='command', help='Command to execute', required=True)
    
    # Select commit command
    select_parser = subparsers.add_parser('select', help='Select least-fuzzed commit')
    select_parser.add_argument('--no-verify', action='store_true', help='Skip binary existence check')
    select_parser.add_argument('--json', action='store_true', help='Output as JSON')
    
    # Increment fuzz count command
    increment_parser = subparsers.add_parser('increment', help='Increment fuzz count and manage schedule')
    increment_parser.add_argument('commit', help='Commit hash')
    
    args = parser.parse_args()
    
    if args.command == 'select':
        commit_to_fuzz, latest_build = run_fuzzer(args.solver, verify_binary=not args.no_verify)
        if args.json:
            import json
            result = {
                'commit_to_fuzz': commit_to_fuzz,
                'latest_build': latest_build
            }
            print(json.dumps(result))
        else:
            if commit_to_fuzz and latest_build:
                # Output both values, separated by space (workflow can parse)
                print(f"{commit_to_fuzz} {latest_build}")
                sys.exit(0)
            else:
                # No commit found is not an error - just exit successfully
                sys.exit(0)
    elif args.command == 'increment':
        try:
            increment_fuzz_count_and_manage(args.solver, args.commit)
        except S3StateError as e:
            print(f"❌ S3 State Error: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"❌ Error: {e}", file=sys.stderr)
            sys.exit(1)

