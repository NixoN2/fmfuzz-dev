; COMMAND-LINE: --bitblast=eager
; COMMAND-LINE: --bitblast=eager --bv-solver=bitblast-internal
; EXPECT: sat
(set-logic QF_BV)
(declare-const x (_ BitVec 4))
(check-sat)
