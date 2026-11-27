---
name: spartan-gpu-jobs
description: >-
  Submit, resubmit, schedule, or optimize GPU jobs and GPU job arrays on University
  of Melbourne Spartan HPC. Use when a user asks to submit GPU inference or
  training, queue classifier/model jobs, choose the fastest suitable GPU partition,
  batch GPU tasks, use MIG or preemptible GPUs, or recover failed GPU jobs. Trigger
  examples include "submit this GPU job", "submit these GPU jobs", "resubmit the
  classifier", "queue training on Spartan", and "find the shortest GPU queue".
  For CPU-only work use spartan-cpu-jobs; for mixed pipelines use both skills for
  their respective stages. Infer GPU need from the real program, not the word Python.
---

# Spartan GPU job submission

## Goal and boundaries

Optimize time to correct, persistent results: queue delay + runtime + restart cost.
The fastest GPU or earliest estimated start is not necessarily the fastest finish.
Follow project instructions and existing scripts before introducing new conventions.

- Treat the shell as a login node. Run inference, training, heavy preprocessing,
  installation and benchmarks only in compute allocations.
- A submission request authorizes the requested workload, not duplicate production
  jobs, destructive cleanup, changed scientific parameters, or cancellation of
  unrelated allocations. Advice/script-drafting requests alone do not authorize jobs.
- Resolve the actual account, paths, environment and workload from project/scheduler
  evidence. Do not invent them or use an account/partition without access.
- Reusable code belongs in `scripts/` unless specified otherwise. Follow the project's
  environment manager; this user's projects use `uv`.
- Temporary clips/caches belong on scratch. Persistent outputs, manifests,
  checkpoints and job records belong on the project allocation (`data/` where required).

## 0. Mandatory existing-job preflight

Before every submission or resubmission, including pilots, inspect the user's queued
and active jobs with `squeue -u "$USER"`. For potentially related jobs, inspect
`scontrol show job JOB_ID`, array element states, dependencies, command/work directory,
manifest, model/parameters and output paths. Use accounting and submission records
for recent terminal jobs. Do not identify duplicate work by job name alone.

Compare the requested inputs and output destinations against existing jobs and
dispatchers. Treat pending, running, configuring, suspended, requeued and completing
work as potentially active until its actual state and coverage are established.

| Existing work/state | Required action |
| --- | --- |
| Same workload running or otherwise active | Reuse/monitor it; do not submit overlapping work or change its live code/inputs. |
| Same workload pending | Inspect the pending reason, dependencies and request. Keep a valid job; safely adjust pending resources/partition only when compatible and justified. Verify GPU/profile allocation after adjustment. |
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
- If only the running GPU/CPU/RAM/job/account cap is occupied, submit a valid request
  when submission quota allows. Slurm can keep it pending until capacity becomes
  available; do not manually wait for unrelated work to finish or cancel it.
- If the queued-plus-running submitted-task quota is exhausted, the request may be
  rejected. Pack work into fewer tasks or use bounded submission cohorts; preserve
  full requested coverage. Do not repeatedly retry an unchanged rejected request.
- Verify actual acceptance and pending reason. A waiting resource cap is not a
  broken job, but invalid resources or impossible dependencies need diagnosis.

## 1. Establish inputs, code and recovery scope

Read the model runner, output contract, existing Slurm scripts and environment.
Determine whether the program needs a GPU and supports multiple GPUs. Do not request
multiple GPUs unless the application actually distributes work across them.

For recovery, inspect `squeue`, accounting, failure logs and expected output coverage.
Create an immutable manifest of missing/failed work using stable IDs and the runner's
actual output mapping. Reuse validated completed outputs; check for active overlapping
jobs. Terminal tasks alone do not establish output correctness or corpus completion.

Keep a stable code/environment snapshot for queued jobs. Slurm stores the batch script,
not every external script or model path it invokes. Renaming a runner while an array
is queued can break all later tasks. Record revision/snapshot, checkpoint identity,
environment, manifest, parameters and job IDs.

## 2. Establish GPU suitability with a representative pilot

Reuse credible measurements when available; otherwise run a small compute-node pilot
with typical and longest/largest inputs. Exercise incomplete batches and other relevant
boundaries. Validate outputs, not just process exit.

Measure peak GPU memory separately from CPU RAM, inference/training throughput,
preprocessing/model-load overhead, GPU utilization and CPU/I/O bottlenecks. Include
CPU RAM used by audio decoding and worker processes. Do not equate GPU memory fit
with adequate throughput, or a tiny-input smoke with worst-case memory safety.

Candidate allocations:

| Workload | Candidates and checks |
| --- | --- |
| Small independent inference | MIG slice if measured memory and throughput fit |
| Moderate inference | L40S/A100; compare real throughput and queue evidence |
| Large-memory work | Full A100/H100 when memory/compute requirements justify it |
| Restartable work | Preemptible partitions with durable resumable state |
| Work that cannot tolerate interruption | Suitable non-preemptible allocation |

Check CUDA/framework/driver compatibility, supported kernels, precision and model
behavior on the target GPU. GPU types and MIG profiles are live inventory, not
permanent skill constants. Test the relevant slice profile explicitly.

For training, checkpoint model, optimizer, scheduler and other required resume state.
For inference, publish per-unit outputs atomically and skip verified completed work.
`--requeue` alone does not make an application resume safely.

## 3. Read live partition policy and compare exact requests

Read official documentation and current scheduler state. Useful lightweight queries,
adapted to the account and task:

```bash
sinfo -o '%P %a %l %G %C'
scontrol show partition PARTITION
scontrol show config
sacctmgr show assoc where user="$USER" account=ACCOUNT
sacctmgr show qos
sbatch --test-only --partition=PARTITION --gres=GPU_REQUEST ACTUAL_JOB_SCRIPT
```

Inspect allowed accounts/QOS, partition QOS, walltime, memory/CPU/GPU caps, submitted
job/task limits and array index limits. If a query is unavailable, use other scheduler
and documentation evidence and state the uncertainty. Public versus specialist access
must be checked; never assume entitlement from an apparently idle node.

Compare all relevant compatible candidates, including MIG, preemptible and ordinary
partitions. Use the real script, GPU profile/count, CPU/RAM, walltime, constraints,
exports and array shape. A single-task estimate does not describe full-array throughput.
Idle nodes do not prove that the requested resource combination is schedulable.

For example, full-GPU requests commonly use `--gres=gpu:1`; a supported 20-GB MIG
profile may use `--gres=gpu:1g.20gb:1`. Select profiles from current inventory.
Do not carry a MIG-specific GRES unchanged onto a full-GPU partition.

Choose by queue + measured runtime + interruption cost. Consider multiple eligible
partitions only when the request is valid on each; test the combined request too.
Do not assume combining partitions improves the estimate. Starts are snapshots,
not promises. Reassess pending work when materially better compatible capacity becomes
available; preserve job records and verify actual allocations after a migration.
Never cancel running work or duplicate it merely to chase a speculative estimate.

## 4. Tune batching before adding competing GPU processes

Distinguish three independent controls:

1. **Inference batch size:** clips/examples processed together by one model.
   For every batch-capable GPU inference workload, MUST explicitly try to improve
   output throughput by increasing batch size toward the largest size that safely
   fits the target allocation. A partition has no universal batch-size limit:
   use the actual GPU/MIG profile, model, precision and representative input shapes.
   Reuse credible measurements for the same workload/allocation when available;
   otherwise benchmark in an isolated compute-node pilot, never a live production
   worker or the login node. Advice-only requests require recommending this sweep,
   not submitting jobs without authorization.
   - Measure the current/default batch as a baseline, then progressively increase
     (for example, double) the batch size. Do not stop merely because the default
     fits or the first larger batch gives little improvement. Continue toward the
     safe-memory boundary or a documented application batch limit; if the sweep
     cannot be completed, state the untested range and limiting prerequisite.
   - Retain explicit GPU-memory headroom (normally at least20%) and sufficient
     host RAM. Stop unsafe candidates before launch when their memory requirement
     is established; otherwise handle pilot OOM as a rejected candidate, preserve
     measurements and refine between the last safe and first unsafe sizes when
     useful. Never OOM production to discover a limit.
   - Measure end-to-end output throughput, not just model-call speed, on matched
     long/largest inputs with repeated timings. Validate score/output agreement,
     exact coverage and incomplete final batches at candidate sizes.
   - Select the fastest validated safe batch, not automatically the largest.
     Prefer the smaller batch when throughput is effectively tied. Report the
     largest tested safe size, any rejected/untested sizes, memory headroom and
     why the selected size wins; do not call a truncated sweep the maximum.
   - With a generic GPU request, validate the chosen batch on every eligible
     profile or restrict the request to validated hardware; do not assume the
     scheduler will allocate the largest-memory profile.
   For training, batch changes can alter optimization/scientific behavior;
   do not change them without appropriate authorization.
2. **Job packing:** several recordings/inputs handled inside one GPU allocation.
   Amortize scheduling/startup overhead and reduce submission counts. Reuse a loaded
   model across inputs where supported. Balance batches by clip count/duration or measured
   cost rather than only record count. Keep packed duration within walltime with margin.
3. **Concurrent model processes:** may hide CPU/I/O gaps but duplicate model memory and
   contend for compute. Benchmark before enabling. Prefer improving data feeding/batching
   first; MIG supplies isolated sharing when an appropriate slice fits.

A GPU allocation does not automatically parallelize the program. Limit CPU workers
and nested BLAS/OpenMP threads to the requested resources. If GPU utilization is low,
find whether decoding, I/O, model loading or inference is the bottleneck. Consider
CPU preparation followed by GPU inference and CPU aggregation, using valid dependencies,
instead of holding GPU allocations idle for long CPU stages.

## 5. Respect submission quotas and resource throttles

An array `%K` limits running elements, not submitted elements. Each element counts
toward the applicable submitted-task limit even while pending. The array index ceiling,
per-user submitted limits, running-resource limits and account caps are separate constraints.
Inspect current policy and other active jobs; do not hard-code the historical 200-task
full-GPU limit or assume the same quota applies to MIG/CPU partitions.

Prefer packed tasks or bounded submission cohorts when one-task-per-record exceeds
quota. Ensure the complete manifest is covered; reducing scope to fit is not a solution.
Avoid long comma-separated array requests when ranges or a manifest-to-task mapping
are clearer. Do not repeatedly submit unchanged requests after quota/path-length errors.

Choose array concurrency from resource/account headroom and measured filesystem behavior,
not merely the maximum scheduler permits. Too much parallel decoding/file creation can
slow the entire workload. Stage disposable data appropriately and retain durable outputs.

## 6. Submit and verify end to end

1. Validate scripts, paths, environment, manifest coverage, dependency conditions and
   resource requests. Do not launch obsolete dispatchers that overlap a new recovery.
2. Smoke changed code/environment on a suitable compute allocation. Exercise meaningful
   boundaries and check actual output correctness; gate the full workload on the pilot
   where practical. Do not use a fake no-op as evidence of GPU inference.
3. Submit once; save exact returned job IDs and allocation/input scope persistently.
4. Confirm acceptance and state with `squeue`/`scontrol`. Diagnose submission errors before
   retrying. Use `afterok` when downstream stages require successful outputs; `afterany`
   only for recovery/gates that explicitly handle failures.
5. Observe initial execution and inspect logs. Verify actual GPU/profile allocation using
   scheduler TRES and, inside the allocation, GPU diagnostics where appropriate.
6. Use `my-job-stats -j JOB_ID -a`/`-c` for supported utilization monitoring and `sacct`
   for terminal exit/resource evidence. Monitor the right array task, not just its parent.
7. Validate produced results and corpus coverage. Recover only missing/failed work.

If pilot/production tasks are pending, report pending acceptance separately from
execution verification. Never run the heavy workload on the login node to bypass a queue,
promise an estimated start, or claim completion from a submitted job ID.

## Delivery checklist

Report job IDs, selected partition and GPU/MIG profile, CPUs/RAM/walltime, task count,
packing and concurrency, manifest/result locations, comparison evidence, observed state,
smoke results or pending verification, and preemption/restart risks. Distinguish GPU RAM
from CPU RAM and tasks/allocations from physical GPU count when using MIG.
Include the inference batch sweep or reused measurements, baseline/selected
throughput, largest tested safe batch, memory headroom and any untested boundary.

## Official sources

- [Spartan HPC](https://rcs-knowledge-hub.atlassian.net/wiki/spaces/KB/pages/880345089/Spartan+HPC)
- [Submitting jobs](https://rcs-knowledge-hub.atlassian.net/wiki/spaces/KB/pages/957775887/Submitting+jobs)
- [Accessing GPUs](https://rcs-knowledge-hub.atlassian.net/wiki/spaces/KB/pages/958365708)
- [Monitoring jobs](https://rcs-knowledge-hub.atlassian.net/wiki/spaces/KB/pages/958431233/Job+Monitoring)
