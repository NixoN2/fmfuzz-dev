; COMMAND-LINE: -q
; EXPECT: sat
(set-logic QF_LIA)
(assert (> 2 1))
(check-sat)
