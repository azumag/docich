# Reviewed pre-synced submodule reconcile

Production deploy remains fail-closed when the docich worktree is not internally consistent with its recorded root commit. A common bounded case is that the owner has already synchronized the reviewed Soren checkout/live projection before the parent docich gitlink is merged.

The automatic recovery in `vm-operations.yml` is intentionally limited to failed **push-triggered production deploys**. It queries the read-only production status and proceeds only when there are no pending repair records, the live root SHA is an ancestor of the candidate, and the `games/soviet_now` gitlink actually advances. The VM-side helper then independently requires the root to remain at that exact old SHA with no tracked root drift, and the Soren checkout to be clean and exactly at the reviewed target commit. Only then is the checkout moved back to the old gitlink so the normal gateway can retry the canonical deployment transaction.

The helper does not modify `/home/ubuntu/soren`, deployment state, services, secrets, or network configuration. The retry still uses the existing root-owned gateway, which validates the projection bytes and modes and rolls back on any unknown state.
