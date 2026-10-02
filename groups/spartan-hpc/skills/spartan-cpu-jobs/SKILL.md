---
name: spartan-cpu-jobs
description: >-
  Submit, resubmit, schedule, or optimize CPU jobs and CPU job arrays on University
  of Melbourne Spartan HPC. Use when a user asks to submit CPU work, run a script
  on Spartan without GPUs, choose the fastest suitable CPU partition, batch CPU
  tasks, or recover failed CPU jobs. Trigger examples include "submit this CPU
  job", "submit these CPU jobs", "queue the preprocessing", "resubmit the CPU
  array", and "find the shortest CPU queue". For GPU inference or training use
  spartan-gpu-jobs; for mixed pipelines use both skills for their respective stages.
---

# Spartan CPU job submission

## Goal and boundaries

Optimize time to correct, persistent results: queue delay + runtime + restart cost.
Do not equate the shortest estimated queue, most CPUs, or largest node with the
best allocation. Follow project instructions and existing scripts first.

- Treat the current shell as a login node. Run heavy computation, preprocessing,
  installation and benchmarks in compute allocations, not on the login node.
- Submission requests authorize the requested work, not duplicate production jobs,
  deleting results, changing scientific parameters, or canceling unrelated jobs.
- A request for advice or a draft script does not authorize submitting jobs.
- Use the configured account and actual paths from repository scripts or scheduler
  associations; do not invent account names or commands.
- Reusable project code belongs in `scripts/` unless the project specifies otherwise.
  Use the project's package manager; for this user's projects it is `uv`.
- Scratch is temporary. Keep the durable manifest, results, checkpoints and submission
  record in the project's persistent allocation (`data/` where required).

## 0. Mandatory existing-job preflight

Before every submission or resubmission, including pilots, inspect the user's queued
and active jobs with `squeue -u "$USER"`. For potentially related jobs, inspect
`scontrol show job JOB_ID`, array element states, dependencies, command/work directory,
manifest, parameters and output paths. Use accounting and submission records for
recent terminal jobs. Do not identify duplicate work by job name alone.

Compare the requested inputs and output destinations against existing jobs and
dispatchers. Treat pending, running, configuring, suspended, requeued and completing
work as potentially active until its actual state and coverage are established.

| Existing work/state | Required action |
| --- | --- |
| Same workload running or otherwise active | Reuse/monitor it; do not submit overlapping work or change its live code/inputs. |
| Same workload pending | Inspect the pending reason, dependencies and request. Keep a valid job; safely adjust pending resources/partition only when compatible and justified. Verify the result. |
| Partial overlap | Submit only uncovered units, excluding work already assigned to active jobs and valid completed outputs. |
| Unrelated jobs queued/running | Leave them alone; account for their applicable submission/resource quotas, but do not treat them as duplicates. |
| Related jobs terminal | Verify outputs and recover only missing/invalid units; terminal state is not output validation. |

Do not cancel user-owned or unrelated jobs without authorization. If replacement of
a pending job is necessary, establish permission, prevent overlapping execution,
preserve records and outputs, and verify cancellation before replacement. Refresh
queue state immediately before changing a job because it may have started.
For jobs created during this request, safe replacement of still-pending trial jobs
is allowed when needed to complete the requested submission; never extend that to
running work or unrelated jobs.

Distinguish **submission headroom** from **running-resource headroom**:
- If only the running CPU/RAM/job/account cap is occupied, submit a valid request
  when submission quota allows. Slurm can keep it pending until capacity becomes
  available; do not manually wait for unrelated work to finish or cancel it.
- If the queued-plus-running submitted-task quota is exhausted, the request may be
  rejected. Pack work into fewer tasks or use bounded submission cohorts; preserve
  full requested coverage. Do not repeatedly retry an unchanged rejected request.
- Verify actual acceptance and pending reason. A waiting resource cap is not a
  broken job, but invalid resources or impossible dependencies need diagnosis.

## 1. Establish the workload and recovery scope

Read the entry point, existing Slurm scripts and output contract. Determine whether
work is single-threaded, multithreaded, a local process pool, independent records,
or explicitly distributed/MPI. Request no GPU for CPU-only work.

For resubmission, inspect `squeue`, accounting, failure logs and expected outputs.
Build an immutable manifest of missing/failed work, using stable identifiers and the
actual output mapping. Reuse valid completed results. Check for active overlapping
jobs before submitting. A terminal Slurm task is not proof of a valid output.

Freeze the code/environment used by queued jobs, or keep their referenced paths
stable. Slurm snapshots the batch script, not every file it invokes. Record the code
revision or snapshot path, environment, manifest, parameters and job IDs.

## 2. Measure and size CPU resources

Prefer existing measurements. If the resource need or changed path is unverified,
run a small compute-node pilot with typical and high-cost inputs. Check actual
outputs, elapsed time, peak RAM, CPU utilization and I/O waiting.

Match the request to application parallelism:

| Application | Slurm shape |
| --- | --- |
| Serial script | One node, one task, one CPU |
| Threads/local worker pool | One node, one task, `--cpus-per-task=N` |
| Independent work | Array or packed local workers |
| Distributed/MPI | Multiple tasks/nodes only when the program supports it |

Requesting CPUs does not create threads or workers. Pass the allocated CPU count to
supported application settings. Benchmark a few CPU counts when useful; compare
throughput and core-hours as well as elapsed time. Stop increasing cores when
scaling saturates or I/O becomes the bottleneck.

Prevent nested oversubscription. For a process pool, consider BLAS/OpenMP threads
of one per worker (`OMP_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`,
`MKL_NUM_THREADS=1`). For a multithreaded numerical application instead, use its
supported settings, often `OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK`. Do not force
one strategy onto every application.

Specify realistic memory and walltime with a measured margin. `--mem` is CPU RAM
per node; `--mem-per-cpu` scales with allocated CPUs. Worker processes may duplicate
large data. Do not assume a historical default RAM-per-core is current policy.
Avoid oversized requests that restrict placement and undersized ones that fail.
Use high-memory partitions only when the measured requirement warrants them.

## 3. Read live policy and compare eligible CPU queues

Consult the official sources below and live scheduler state. Historical partition
names, quotas and queue estimates are examples, not constants.

Useful lightweight commands, adapted to the actual account:

```bash
sinfo -o '%P %a %l %C'
scontrol show partition PARTITION
scontrol show config
sacctmgr show assoc where user="$USER" account=ACCOUNT
sacctmgr show qos
sbatch --test-only --partition=PARTITION ACTUAL_JOB_SCRIPT
```

Inspect partition and association QOS, access, walltime, CPU/memory limits,
submitted-task and running-resource caps. If a query is unavailable, use the
available scheduler evidence and documentation and state the uncertainty.

For ordinary public CPU work, compare eligible partitions such as `sapphire` and
`cascade`; consider `bigmem` only when needed. Do not assume entitlement to
specialist partitions. Test the actual script, account, CPUs, RAM, walltime,
constraints, exports and array shape. Idle cores do not prove a requested allocation
fits. CPU generations can differ in throughput.

Choose the suitable request with the best evidence for completion time. Short,
honest walltime requests may fit backfill opportunities; do not understate runtime.
Queue estimates are snapshots, not guaranteed starts. If considering multiple
partitions, test the combined request too.

## 4. Pack and balance independent work

Pack very short work into fewer allocations to amortize startup/scheduling overhead.
Use bounded workers only where they improve throughput. Keep long or variable
work in smaller recoverable units. Balance by file size, audio duration, row count
or measured runtime rather than file count alone.

An array `%K` throttle limits active elements, not submitted ones. Every element
counts toward the applicable submission quota; a GPU partition's quota is not a
CPU quota. Include other queued/running jobs when checking headroom. Also check the
array index ceiling separately. Avoid repeatedly issuing rejected submissions.

Bound shared-filesystem concurrency. If checksums, copying or decoding stop scaling,
reduce workers instead of increasing CPU allocation. Stage temporary reusable files
on scratch when beneficial; never make scratch the only copy of a durable result.

Separate CPU preparation, GPU computation and CPU aggregation with dependencies
when appropriate. Use `afterok` for required successful inputs; `afterany` only for
steps that explicitly inspect and handle predecessor failures.

## 5. Submit and verify

1. Validate paths, environment, manifest coverage, script syntax and resource shape.
2. If code or environment changed, exercise the changed path in a compute pilot and
   verify consumer-visible outputs before releasing the full workload where practical.
3. Submit once; persist returned job IDs and the exact allocation/input scope.
4. Confirm Slurm accepted the request with `squeue`/`scontrol`; inspect actual requested
   resources and dependency state. Diagnose rejection before retrying.
5. Observe initial execution/logs when resources start. Use
   `my-job-stats -j JOB_ID -a` and `-c` for supported utilization monitoring;
   `sacct` supplies terminal state, exit codes and recorded resource usage.
6. Verify successful outputs, not just exit codes. Recover only unresolved work.

Do not promise a start time or claim execution/completion for a merely pending job.
If a pilot or production request remains queued, report its state and the verification
limit explicitly instead of performing the computation on the login node.

## Delivery checklist

Report job IDs, partition, tasks/CPUs/RAM/walltime, array concurrency and packing,
manifest/result locations, why the request was chosen, observed state, smoke evidence
or pending verification, and preemption/restart risks if applicable. Do not fabricate
measurements or silently alter workload parameters to make submission succeed.

## Official sources

- [Spartan HPC](https://rcs-knowledge-hub.atlassian.net/wiki/spaces/KB/pages/880345089/Spartan+HPC)
- [Submitting jobs](https://rcs-knowledge-hub.atlassian.net/wiki/spaces/KB/pages/957775887/Submitting+jobs)
- [Monitoring jobs](https://rcs-knowledge-hub.atlassian.net/wiki/spaces/KB/pages/958431233/Job+Monitoring)
