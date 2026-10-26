# tombstone_retention

Three records that the policy treats three different ways. The account tombstone
may keep its stable `subject_reference` but still exposes a name and an email, so
`rule_15` requires redaction. The security audit record is inside its retention
period, so `rule_12` requires it to stay and be verified present. The backup
archive already has `restore_allowed: false`, so `rule_14` is satisfied and only
the restore-blocked check remains.
Expected: `INCOMPLETE`, driven solely by the tombstone's exposed identifiers.
