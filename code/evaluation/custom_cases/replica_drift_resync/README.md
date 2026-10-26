# replica_drift_resync

The deletion worker reported `SUCCESS` on the account, and the primary copy no
longer holds it - but the failover replica still serves the profile, so the
record is not gone. An active connector has meanwhile repopulated the search
document, which the activity stream states outright.
Expected: `INCOMPLETE` with a remaining record for the drifted account, a
remaining record for the re-synced search document, and an active data flow that
must be disabled before the records are touched.
