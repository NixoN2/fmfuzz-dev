; COMMAND-LINE:
; EXPECT: unsat
(set-logic QF_LIA)
(assert (> 1 2))
(check-sat)
