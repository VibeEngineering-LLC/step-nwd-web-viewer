TASK: Write a complete pytest module `test_db.py` for the module `hosting_app.db` described above (Python 3.12; no markdown fences, no explanations, output ONLY code; comments and test docstrings in Russian). Use fixtures `conn`, `invite`, `data_dir` from conftest (they exist: `conn` is an initialized connection, `invite` yields `(token, invite_row)`). Import with `from hosting_app import config, db`. Use the `now=` parameters instead of sleeping. Each case below is a separate test function with an assertion on the exact stated outcome:
1. `new_token()` returns a string of at least 22 characters, two calls differ; `hash_token("a")` equals the sha256 hex of `a` (64 chars).
2. `add_invite` stores no plaintext token: `SELECT * FROM invites` rows converted to strings do not contain the token; `get_invite(conn, token)` finds it; `get_invite(conn, "wrong")`, `get_invite(conn, "")`, `get_invite(conn, None)` return None.
3. After `revoke_invite(conn, id)` the token no longer works (None). `revoke_invite(conn, 9999)` raises `KeyError`.
4. `revoke_invite(conn, id, purge=True)` returns the ids of all that invite's models and they are gone from the table.
5. `create_model` returns an id of at least 22 characters; row status `queued`; `expires_at == now + RETENTION_DAYS*86400` for `now=1_000_000`.
6. `count_active_models`: with models created at now=1000, one `done`, one `failed`, one `queued`: count is 2; with `now` after expiry of all (now + 31 days) count is 0.
7. `count_uploads_since`: models created at t=1000 and t=1000+90000: with `now=1000+90000+10` and the default window 86400 only the second is counted (count 1); failed ones are counted too.
8. `claim_next`: returns the oldest queued model first (create two with different `now`), the returned row has `status == 'running'` and `attempts == 1`; third call returns None.
9. `reset_running`: a model with attempts 1 goes back to `queued`; a model with attempts 2 becomes `failed` with a non-empty `error`; return value counts both.
10. `extend_model`: succeeds three times for a `done` model (each adds RETENTION_DAYS*86400 to the later of expires_at/now), the fourth returns False; returns False for another invite's id, for a `queued` model, and for an unknown id.
11. `expired_models(now)` includes expired `done`/`failed` but never `running`; `stale_failed(conn, 7, now)` includes only `failed` older than 7 days.
12. `finish_model` with status `queued` raises `ValueError`; `stats` returns correct `models_total`, `by_status` for a prepared set.
13. Concurrency sanity: two connections to the same DB file (both from `db.connect()`) — `claim_next` called from both never returns the same model twice for 5 queued models (alternate calls).
