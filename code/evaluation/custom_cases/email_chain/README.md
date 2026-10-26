# email_chain

The subject changed email twice (old -> mid -> new) and files the request with
the **oldest** address, which only `user_audit_log.csv` knows about.
Three live records each carry a different one of the three addresses, so a
pipeline that resolves only the current identity finds at most one of them.
Expected: all three found, `INCOMPLETE`, with the analytics profile anonymised
rather than deleted because `rule_07` says so.
