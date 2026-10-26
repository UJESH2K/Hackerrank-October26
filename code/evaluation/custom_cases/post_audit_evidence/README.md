# post_audit_evidence

Two snapshots of the same system: the profile is still there on 5 September and
gone by 20 September. The case's `audit_at` is 10 September, so the later
snapshot is inadmissible however convenient its answer is - the deletion had not
happened by the cutoff.
A pipeline that takes the newest evidence it can find reports this case
`COMPLETE`.
Expected: `INCOMPLETE`, citing the 5 September snapshot.
