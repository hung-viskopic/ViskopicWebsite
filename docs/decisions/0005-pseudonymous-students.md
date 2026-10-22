# 0005 Pseudonymous students

**Status:** accepted · 2026-10-15

Students get a random pseudonym (`S-XXXXXXXX`). Analysis code will only ever see the pseudonym and the internal
id. The real student number and name are encrypted and only decrypted for staff screens. Reviewers can find an
existing student by exact student number (keyed hash) but can only browse students on courses they have access to.
