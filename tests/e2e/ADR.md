# Architecture Decision Records - e2e test runner

Records the significant decisions behind this package's design. Each entry is
independent - later ones don't supersede earlier ones unless stated. Written
after the fact (during the rewrite from a single 660-line `tests/run_e2e.py`
into this package), so context sections describe what motivated the change,
not a live design discussion.

## ADR-001: Split into a package organized by concern

**Status:** Accepted

**Context:** `run_e2e.py` had grown to 660 lines of loosely-ordered functions
covering AWS discovery, SSH polling, ansible invocation, VPN verification,
terraform lifecycle, and reporting, with no grouping - unrelated concerns sat
next to each other in whatever order they'd been added.

**Decision:** Split into `tests/e2e/{models,config,aws,ssh,provisioning,
verification,terraform,report,display,orchestrator}.py`, each owning one
concern. `tests/run_e2e.py` became a thin entry point (`from
e2e.orchestrator import main`) so the documented invocation (`uv run
tests/run_e2e.py --config ... --ssh-key ...`) didn't change.

**Consequences:** More files to navigate, but each is short and single-
purpose. `orchestrator.py` is the only module that imports from most of the
others - it's the place to look for how the pieces fit together.

## ADR-002: Live status board instead of streaming raw Ansible output

**Status:** Accepted

**Context:** The console showed every instance's Ansible output interleaved
with a hostname prefix. With several instances provisioning concurrently,
this was hundreds of interleaved task lines with no way to tell what was
actually happening, or whether the run was stuck versus just slow - the
motivating complaint was having to `^C` a run that "looked hung" but wasn't.

**Decision:** Added `rich` as a dependency. `display.StatusBoard` renders a
`Live`-updating table (instance, phase, current task, elapsed time, result),
rebuilt from live `InstanceInfo` state on a timer from a dedicated thread -
not Rich's own auto-refresh, since a `Table` is a static snapshot and
redrawing the same one wouldn't reflect the underlying data mutating in
other threads. Per-instance Ansible output moved out of the console entirely
(see ADR-003) so it no longer competes with the table for the terminal.

**Consequences:**
- All free-text cell values (display names, task names) are wrapped in
  `rich.text.Text()`, never passed as raw strings. Rich parses raw strings as
  markup by default; a real Ansible task name containing `[...]`-looking text
  (`TASK [kyl191.openvpn : Enable CRB repository ...]`) was silently mangled
  during testing - only `"TASK "` survived, the rest swallowed as an
  unrecognized style tag. `Text()` never parses markup, so this is safe by
  construction rather than something that has to be remembered per call site.
- The Detail column (current task/curl check) is by far the most variable-
  length field. Sizing it to its own content on every refresh made the whole
  table jitter on each redraw. Fixed by ordering it last, expanding the table
  to the terminal's full width, and giving Detail the sole `ratio` so it
  absorbs the leftover space (truncating with `…`) instead of dictating
  table width.

## ADR-003: Durable output under /tmp, not the repo tree

**Status:** Accepted

**Context:** Previously nothing was written to disk - only the console saw
Ansible output, so a closed terminal or an unattended run lost the entire
history. Once ADR-002 moved per-instance output out of the console, it
needed somewhere durable to go instead.

**Decision:** Each run gets `/tmp/ansible-openvpn-e2e/<run-timestamp>/`:
`run.log` (everything the script logs), one subdirectory per scenario
holding each instance's raw Ansible log, a `<name>-<id>-timings.log`
per-task breakdown (ADR-008), and the final markdown report. Chosen over
writing into the repo tree (as `tests/e2e_report.md` previously did)
specifically so it doesn't need `.gitignore` entries or get accidentally
committed. Nothing in the code deletes this directory - it's the whole
point of the run and needs to outlive it, unlike the ephemeral
per-verification OpenVPN client log/pid files in `verification.py`, which
genuinely are cleaned up after each use.

**Consequences:** Output doesn't show up next to the repo by habit; you have
to know to look under `/tmp/ansible-openvpn-e2e/`. The final report path is
logged at the end of every run specifically so this isn't a problem in
practice.

## ADR-004: Human-facing display name, not the AWS DNS name

**Status:** Accepted

**Context:** Every log line and (later) status-board row was prefixed with
`instance.hostname` - an AWS-generated string like
`28zmx-sj4x7-0nn5o-3vl10-hogah.us-east-2.ip.aws`. Meaningless to a human
scanning output for which distro is failing.

**Decision:** Added `InstanceInfo.display_name`: the OS detected over SSH
(`PRETTY_NAME`, e.g. `"Fedora Linux 44 (Cloud Edition)"`) once known, falling
back to the EC2 `Name` tag (e.g. `"fedora-44-x86-ipv4only"`, set from
terraform's `instance_config` key - already OS/scenario-descriptive) before
SSH succeeds. `hostname` (the real DNS name/IP) is unchanged and still used
for actual networking - SSH targets, the Ansible inventory,
`openvpn_server_hostname`.

**Consequences:** `display_name` is free text meant for humans, not
filesystems - a detected `PRETTY_NAME` can contain a literal `/` (`"Debian
GNU/Linux 13 (trixie)"`), which crashed a log-file open the first time this
ran for real, read as a subdirectory that didn't exist. Any code building a
path from `display_name` has to run it through
`provisioning._safe_filename_component` first; it isn't filesystem-safe on
its own.

## ADR-005: Terraform applies in place; one destroy, at the end

**Status:** Accepted

**Context:** Each scenario (dual-stack, IPv6-only, IPv4-only) previously got
its own full `terraform destroy` followed by `terraform apply` - rebuilding
the VPC, security groups, and key pair from scratch every time, even though
those are identical across scenarios and only each scenario's
`instance_config` actually differs.

**Decision:** Each scenario's var-file is applied in place, in the same
terraform workspace, in sequence. Terraform's own diff tears down the
previous scenario's now-absent instances and creates the new ones; the
shared base layer is never touched. A single `terraform_destroy` runs once
after every scenario has run (or failed), in a `finally`, using the last
*successfully* applied var-file - not blindly the last configured one, in
case the final scenario's apply itself failed.

**Consequences:** Faster (three fewer full VPC rebuilds per run) and doesn't
change behavior on a scenario failure - the loop already continued past a
failed scenario before this change, this only removed the redundant
interstitial destroy.

## ADR-006: SSH-wait decoupled from provisioning, per instance

**Status:** Accepted

**Context:** `run_scenario` ran two sequential `ThreadPoolExecutor` passes:
wait for *every* instance's SSH to become ready, then provision *every*
instance. Since each instance's Ansible run is already its own subprocess
(independent of every other instance), this batching served no purpose other
than making a slow-booting sibling (an IPv6-only instance's cloud-init can
take minutes longer) hold up everyone else. It also meant an instance that
became SSH-ready quickly kept showing "waiting for SSH" long after it was
actually ready, just idling for its siblings.

**Decision:** One `ThreadPoolExecutor` pass, where each instance's own thread
waits for its own SSH readiness and then immediately provisions itself
(`orchestrator._wait_and_provision`) - independent of how long any sibling
takes.

**Consequences:** None negative found; this was a pure win once the
per-instance independence was already true at the provisioning level.

## ADR-007: Role path passed in explicitly, not resolved via a relative YAML path

**Status:** Accepted

**Context:** `tests/ec2.yml` referenced the role as `role: ../kyl191.openvpn`
- this only resolves because Ansible's role search re-appends the checkout
directory's own name to a search-path entry, which breaks silently the
moment the checkout isn't literally named `kyl191.openvpn` (a git worktree,
for instance). Confirmed against real EC2 instances: a run "succeeded" in
~1-5s with an empty `PLAY RECAP` (only `Gathering Facts`, zero role tasks,
exit code 0, no error at all) - the three instances that showed `PASS` were
actually verifying against stale certs left over from an unrelated earlier
run. A bare `..` isn't a fix either - confirmed via `--list-tasks` that it
also silently resolves to a role with zero tasks instead of erroring, for
reasons not fully understood (an apparent Ansible role-path-normalization
quirk).

**Decision:** `provisioning.ROLE_PATH` computes the role's absolute path in
Python (`Path(__file__).resolve().parents[2]`) and passes it via `-e
openvpn_role_path=...`; `ec2.yml` references `{{ openvpn_role_path }}`.
Confirmed via `--list-tasks` that this loads all ~111 tasks correctly,
independent of the checkout's directory name or the process's cwd.

**Consequences:** Because the role then has no `meta`-derived name, Ansible
prefixes every task name with this absolute path instead
(`TASK [/abs/path/to/role : config | ...]`). `provision_instance` strips
everything up to and including `" : "` before using the task name for the
status board or timing file, so this is invisible downstream - but anyone
grepping the raw log for a task name needs to know the prefix is there.

## ADR-008: Per-task timing file, separate from the raw log

**Status:** Accepted

**Context:** The only way to see which step of a run was slow was to grep
timestamps out of the raw Ansible log by hand. The status board already
parses `TASK [...]` boundaries from the same output stream to drive the
Detail column (ADR-002).

**Decision:** `provision_instance` times each task boundary (the gap between
one `TASK [...]` line and the next) and writes it to a sibling
`<log_path>-timings.log` file - one line per task, plus a header with the
instance and total duration. Written in the `finally` block regardless of
success, so a failed or killed run (ADR-009) still shows what it was doing
and for how long right up to the point it died.

**Consequences:** None significant - this is read-only instrumentation on
top of parsing that already happens for the status board.

## ADR-009: Kill a stalled provisioning subprocess

**Status:** Accepted

**Context:** `provision_instance` read Ansible's output with a plain
`for line in process.stdout:` loop - if Ansible produced no output at all
(a hung task), this blocked indefinitely, hanging that instance's thread and
therefore, since terraform destroy waits for the whole scenario, potentially
the rest of the run. This reproduced for real during testing: a Fedora
instance sat on `Gathering Facts` - normally near-instant - for 180s+ in one
run, and did it again in a later run.

**Decision:** Replaced the blocking iterator with
`select.select([process.stdout], [], [], PROVISION_STALL_TIMEOUT)` before
each read. If no output arrives at all for `PROVISION_STALL_TIMEOUT` (90s),
the subprocess is killed and the run is marked failed. This resets on every
line of output, not on an overall deadline - a legitimately slow-but-still-
progressing playbook (the real end-to-end runs observed take 110-140s
total) isn't affected, only a genuinely stuck task is. Verified in isolation
against a synthetic hanging subprocess: stall is detected at the timeout,
the process is actually killed and reaped (not left as a zombie).

**Consequences:** A task that's merely slow rather than hung, but happens to
exceed 90s with zero output in between (none observed in practice; the
slowest real task seen was CA/key generation at a few seconds each), would
be killed as a false positive. If this turns out to matter for a particular
instance type or task, raise `PROVISION_STALL_TIMEOUT` rather than removing
the mechanism.

**Correction (found investigating a real stall):** the original error
message named the *last-seen* task as what was stuck - misleading, since a
real run showed that task's own `skipping:`/`ok:` result line already in the
log before the silence started. The task itself had finished; the actual gap
was ansible taking >90s to produce the *next* task's banner at all - a
connection-layer stall (SSH going quiet between tasks), not a stall inside
whatever task happened to be named last. Fixed by tracking whether the
current task's result line has already been seen (`current_task_done`) and
phrasing the message accordingly: `"stuck on 'X'"` only if X is still
mid-execution, `"stuck after 'X' finished, waiting for the next task"`
otherwise. This matters for diagnosis - don't trust the task name in a stall
message without checking `current_task_done`'s equivalent (whether a result
line for it appears in the raw log) first.

## ADR-010: Isolate each subprocess's stdin

**Status:** Accepted

**Context:** With several `ansible-playbook` subprocesses running
concurrently (one per instance, via ADR-006's `ThreadPoolExecutor`), none
had an explicit `stdin=`, so they all inherited the same shared stdin file
descriptor from this process. Reproduced for real: one instance's run failed
after 0.1s with `ERROR: Ansible requires blocking IO on stdin/stdout/stderr.
Non-blocking file handles detected: <stdin>` - Ansible sets that fd to
blocking mode at startup, and two concurrent processes racing on the same
underlying fd let one flip it out from under another.

**Decision:** `subprocess.Popen(..., stdin=subprocess.DEVNULL)`. A
non-interactive test runner has no reason to feed these processes stdin
anyway, so isolating each subprocess's stdin avoids the shared-fd race
entirely rather than working around its symptoms.

**Consequences:** None - this is strictly safer than the previous
(unintentional) shared-stdin behavior.

## ADR-011: Terraform output to a file too

**Status:** Accepted

**Context:** `terraform_apply`/`terraform_destroy` ran with no output
capture at all, so a full instance-matrix apply/destroy streamed directly to
the console - long enough on its own to bury everything before it, the same
complaint ADR-002 addressed for Ansible output.

**Decision:** Both functions now take a `log_path`, redirect terraform's
stdout/stderr there, and log a short summary line (duration, pass/fail,
where the full log is) - the same shape as `provision_instance`'s summary
line. Log files live under the run's log directory (ADR-003) as
`terraform-apply-<var_file>.log` / `terraform-destroy-<var_file>.log`.

**Consequences:** None - purely additive; terraform's own progress output
was never parsed for anything, just displayed.

## ADR-012: Sort the instance list once, at discovery

**Status:** Accepted

**Context:** Instances were processed and displayed in whatever order the
AWS API happened to return them - effectively unordered, and increasingly
hard to scan as the instance count grew (14 for the full dual-stack matrix).

**Decision:** `aws.get_instances` sorts the returned list by `inst.name`
(the EC2 `Name` tag) before returning it, once, at discovery time - not by
`display_name`. Every other module (the status board, the report) shares
this same list object and iterates it in this order. Sorting by `name`
rather than `display_name` matters: `name` is stable from discovery onward,
while `display_name` starts as this same tag but switches to the detected OS
once SSH succeeds (ADR-004) - sorting by it would visually reorder the
status board mid-run as each instance's SSH check completes at a different
time.

**Consequences:** None - purely cosmetic, no behavior depends on list order.
