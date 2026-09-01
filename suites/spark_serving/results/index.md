# spark_serving results

Every number here was measured by `probe.py` against a live endpoint on the
operator's own hardware. Vendor and aggregator benchmark claims are not
recorded as results.

## 2026-08-31 -- can a sparse MoE beat the dense incumbent on one node?

Both arms measured with the IDENTICAL probe, back to back, on the same box
(one node). Repeat runs shown because one sample proves nothing
about a shared machine.

| arm | model | active params | decode tok/s | repeat | qualifies |
|---|---|---|---|---|---|
| incumbent | `unsloth/Qwen3.8-27B-NVFP4` | 27B (dense) | 20.3 | 22.9 | yes |
| candidate | `Qwen/Qwen3.6-35B-A3B-FP8` (+MTP) | **3B** (MoE) | **64.8** | **64.4** | yes |

**~3x on decode, from a model with MORE total parameters.** Decode on GB10 is
bandwidth-bound -- 273 GB/s, and every token reads every ACTIVE parameter --
so 27B dense to 3B active is roughly 9x less to read per token. The observed
3x rather than 9x is the honest shape of it: prefill, attention and MoE
routing do not shrink with the active-param count.

Both arms hold all three contracts (`json_object`, `tool_calling`, `vision`),
so this is a straight throughput win rather than a trade.

### What the aggregate numbers hide

The incumbent's vision answer leaked reasoning into `content`:

```
"The user wants a one-word answer about the image's color. Th..."
```

It passes the contract only because "red" appears later in that prose. The
candidate returned a clean `'Red'`. For any consumer parsing structured
output that difference matters more than the millisecond gap, and neither a
pass/fail nor a tok/s column shows it -- which is why `detail` is recorded
per contract rather than just the verdict.

### What this does NOT cover

- **Answer quality.** These are serving characteristics. A model can be fast,
  hold every contract, and still review code badly. Nothing here measures
  that.
- **Concurrency.** Single-stream decode only. The interesting agentic number
  is several sub-agents at once, which is not measured.
- **Prefix caching.** An agentic harness re-sends a ~25k-token system prompt
  every turn; the cost of that dominates real sessions and is untested here.
- **Long context.** Probes are short. Nothing says how either arm behaves near
  its window.
- N=2 per arm, one machine, one evening.

## 2026-08-31 -- two-Spark (TP=2) attempt: FAILED, model never served

`@official/minimax-m2.7-nvfp4-vllm` across both boxes. Recorded because a
failed configuration is a result, and this one costs an hour to rediscover.

Two distinct blockers:

1. **`~/.cache/huggingface/modules/` was `root:root` on both nodes**, so any
   model with custom modeling code dies with `PermissionError` -- AFTER the
   image sync and the full ~130 GB download. `sparkrun setup fix-permissions`
   reports OK and does NOT fix it (it repairs `hub/`, not `modules/`).
2. **After fixing that, rank 1 never launched.** The worker container on the
   second host was running `sleep infinity`; rank 0 waited alone and died with
   `DistStoreError: Timed out after 601 seconds waiting for clients. 1/2
   clients joined.` Not a firewall -- `NetworkMode: host`, ufw inactive,
   INPUT policy ACCEPT.

Single-node (`_solo`) on the same tooling works fine, which is how the
candidate above was measured. The fleet's proven multi-node path is SGLang,
not `vllm-distributed`.

**The conclusion this points at:** a 3B-active model reaching 64 tok/s on ONE
box weakens the case for splitting a model across both. Two independent
single-node workers give more aggregate throughput AND survive one box
failing. TP=2 needs to justify itself against that, and here it did not get
the chance to.
