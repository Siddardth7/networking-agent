# Open questions — Fable 5 v0.2.0 pass

1. **Cold-email path live validation (AUDIT-A30).** The June-6 run produced
   zero cold emails because the Hunter free-tier quota for June 2026 was
   exhausted, so none of the cold-email fixes have been validated against a
   live run. The code paths are unit-tested, but a real run with at least a
   few verified email addresses is needed before trusting that channel.
   Blocked on: Hunter quota reset (July 2026) or a paid Hunter key. Marked
   wontfix for this pass.

2. **Critic prompt drift after recalibration.** The new hold rule was
   validated against the June-6 score vectors (33% hold rate). The critic
   *prompt* also changed (grounded_facts guidance), which will shift future
   score distributions in a direction the fixtures cannot fully predict.
   Recommend re-checking the live hold rate on the next real 15-contact run
   before enabling any unattended mode; the deterministic rule constants
   (SEVERE_SCORE / MAX_WEAK_DIMS / MIN_SCORE) are the tuning knobs.
