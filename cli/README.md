# testoria-cli

Push automated test results into Testoria from a CI pipeline, in one command.

```bash
testoria upload reports/junit/api.xml --run 42
```

---

## Install

```bash
pip install 'testoria-cli @ git+https://github.com/gamma-it-solutions/api-testoria@main#subdirectory=cli'
```

Requires Python 3.11+.

---

## Authenticate

**CI — use an API key.** Mint one from the UI, or:

```bash
curl -sX POST https://api.testoria.example/api/v1/api-keys \
  -H "Authorization: Bearer $JWT" -H 'Content-Type: application/json' \
  -d '{"name":"github-actions","project_id":7,"role":"tester"}'
# -> {"key": "tsk_a1b2c3d4_…"}   shown ONCE
```

Store it as a CI secret and expose it as `TESTORIA_API_KEY`:

```bash
export TESTORIA_URL=https://api.testoria.example
export TESTORIA_API_KEY=tsk_a1b2c3d4_…
```

An API key is **revocable**, **scopable to one project**, and **capped at the
`tester` role** — it can never reach user management, project creation, or mint
another key, even when its owner is an admin. Check what yours can actually do:

```bash
$ testoria whoami
ci (id 1) via api_key
  role: tester (capped below the account role 'lead')
  scope: project 1
```

**Humans — log in.** `testoria auth login` stores a JWT in
`~/.testoria/config.yaml` (mode `0600`).

The API key is **never written to disk** — it is read from `--api-key` or
`TESTORIA_API_KEY` only, so a CI secret cannot leak into a mounted home
directory or a committed dotfile.

Resolution order: **flag > environment > config file**. If both a key and a
stored JWT resolve, the key wins and only one credential is ever sent (the
server rejects both headers at once by design).

---

## Upload

```bash
# into an existing run
testoria upload reports/junit/api.xml --run 42

# create the run as part of the upload
testoria upload reports/junit/api.xml \
  --project 7 --create-run "CI #$BUILD_NUMBER" --close-on-finish

# fail the job if a test has no matching case in Testoria
testoria upload reports/junit/api.xml --run 42 --strict

# attach artefacts (file stem must equal a case's automation_id)
testoria upload reports/junit/api.xml --run 42 --attach 'reports/screenshots/*.png'

# machine-readable report
testoria upload reports/junit/api.xml --run 42 --output json
```

Accepts JUnit XML (any framework) or a JSON list of
`{name, classname?, status, message?, stack_trace?, execution_time?}`.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Uploaded |
| `1` | Transport, auth, or usage error |
| `2` | Unmatched test cases — **only** with `--strict` |

### Re-running is safe

Results upsert on `(run, test case)`. Re-running after a network blip writes the
same values and records **no** extra history entries — so a retry is correct, not
a double-submit. The upload `POST` is deliberately not auto-retried; re-run it
yourself when you mean to.

---

## How results find their test case

Matching is server-side, in this order, and the report tells you which rule hit:

| # | Rule | Matches |
|---|---|---|
| 1 | `automation_id` | IDs already stored in dotted form |
| 2 | `dotted(automation_id)` | **pytest node IDs** — the common case |
| 3 | `automation_id` == bare test name | short IDs |
| 4 | `title` | legacy title matching |
| 5 | `dotted(title)` | titles written as node IDs |

Rule 2 exists because JUnit XML does not carry pytest node IDs. pytest emits
`classname="tests.auth.test_auth.TestAuth" name="test_ok"`, while
`automation_id` is usually `tests/auth/test_auth.py::TestAuth::test_ok`. The
server converts the stored ID to dotted form to compare — one direction only,
since the reverse is ambiguous.

**Set `automation_id` to the pytest node ID and matching just works.** A
parametrized test carries its param in `name`
(`test_roles[TESTER]`), so seed one case per variant and each reports
independently.

If a suite is matching by `title`, it is one rename away from reporting nothing:

```
matched: 180 by automation_id_dotted, 12 by title    # <- fix those 12
```

Find the cases that nothing can link to:

```bash
testoria case list --project 7 --unmapped
```

---

## CI recipes

Whatever the system: **run the upload even when the tests fail.** Uploading only
on success loses exactly the results you most wanted.

### GitHub Actions

```yaml
      - name: Run tests
        run: pytest -m api --junitxml=reports/junit/api.xml

      - name: Upload results to Testoria
        if: always()                      # <- the important bit
        env:
          TESTORIA_URL: ${{ secrets.TESTORIA_API_LINK }}
          TESTORIA_API_KEY: ${{ secrets.TESTORIA_API_KEY }}
        run: |
          testoria upload reports/junit/api.xml \
            --project 7 \
            --create-run "CI #${{ github.run_number }} api (${{ github.ref_name }})" \
            --close-on-finish
```

Already running a live reporter that opens its own run? Upload into it instead
of creating a second one:

```yaml
        run: |
          run_id=$(head -1 reports/testoria_run_id.txt)
          testoria upload reports/junit/api.xml --run "$run_id"
```

A separate job to catch mapping drift, where a red build is the right answer:

```yaml
  mapping-drift:
    needs: [api-tests]
    if: always()
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with: { name: reports-api, path: reports }
      - env:
          TESTORIA_URL: ${{ secrets.TESTORIA_API_LINK }}
          TESTORIA_API_KEY: ${{ secrets.TESTORIA_API_KEY }}
        run: testoria upload reports/junit/api.xml --run "$(head -1 reports/testoria_run_id.txt)" --strict
```

### GitLab CI

```yaml
test:
  script:
    - pytest --junitxml=reports/junit.xml
  after_script:                          # runs even when script fails
    - pip install testoria-cli
    - testoria upload reports/junit.xml --project 7 --create-run "$CI_PIPELINE_ID" --close-on-finish
  variables:
    TESTORIA_URL: $TESTORIA_URL
```

### Jenkins

```groovy
post {
  always {
    sh 'testoria upload reports/junit.xml --project 7 --create-run "#${BUILD_NUMBER}" --close-on-finish'
  }
}
```

### Kubernetes Job

Run the upload as a second container sharing the reports volume, so it survives
the test container exiting non-zero. Bound the wait — a test container that dies
before writing XML must not hang the Job:

```yaml
        - name: testoria-upload
          image: your-tests:tag
          command: ["/bin/sh","-c"]
          args:
            - |
              for i in $(seq 1 60); do
                [ -f /app/reports/junit/api.xml ] && break
                sleep 5
              done
              testoria upload /app/reports/junit/api.xml --run "$(head -1 /app/reports/testoria_run_id.txt)"
          envFrom: [{ secretRef: { name: testoria-credentials } }]
          volumeMounts: [{ name: reports, mountPath: /app/reports }]
```

---

## Other commands

```bash
testoria whoami                                   # what these credentials can do
testoria auth login | status | logout
testoria run create --project 7 --name "Sprint 42"
testoria run list --project 7 [--status active]
testoria run show --run 42
testoria run close --run 42
testoria case list --project 7 [--unmapped]
```

Every command takes `--output json` for scripting.

---

## Development

```bash
cd cli
pip install -e '.[dev]'
pytest -q
ruff check testoria_cli tests
mypy testoria_cli
```

The CLI lives in the API repo on purpose: the contract cannot drift when one PR
changes both sides, and `ci.yml` runs these checks on every backend change. It
must never import from `app/` — it talks HTTP like any other client.
