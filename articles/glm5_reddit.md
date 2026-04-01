# [D] Running GLM-5 (744B) on a $5K refurbished workstation at 1.54 tok/s

I wanted to see if GLM-5 could run on non-datacenter hardware. Turns out it can.

**Hardware:** HP Z840 (2015), 2x Xeon E5-2690 v3, 224 GB DDR4, 2x Quadro GV100 32 GB. Total cost ~$5K including GPUs.

**Model:** GLM-5-REAP-50-Q3_K_M (744B params, 40B active MoE, 170 GB GGUF after 50% pruning + Q3 quantization)

**Setup:**
- llama.cpp with `--split-mode layer --tensor-split 0.4,0.6 --n-gpu-layers 25`
- 25 of 80 layers on GPU (split across both), 55 on CPU
- 4K context window

**Result: 1.54 tok/s.** Not interactive, but usable for batch code generation and research tasks.

**Why it works:** MoE means only 40B params active per token. The bottleneck is DDR4 bandwidth (~50 GB/s), not GPU compute. Each token loads ~20 GB of active experts from RAM. Theoretical max ~2.5 tok/s, I get 1.54 (60% efficiency).

**What didn't work:**
- NVLink bridge ($80 wasted) — Z840 routes GPU slots to different CPU sockets, P2P reports TNS. Tried `iommu=off`, `pci=noacs`, `pcie_acs_override` — nothing. Check `nvidia-smi topo -p2p r` before buying.
- `--split-mode row` — needs working P2P. Use `layer` instead.
- 20+ GPU layers — OOM at ~34 GB per GPU. Binary search found 25 as the max with 40/60 split.

**Practical uses at 1.54 tok/s:**
- ARC-AGI-2 code generation (fire and wait)
- Paper review / summarization
- Research Q&A with RAG
- Batch overnight processing

**Not useful for:** interactive chat, real-time applications

The key realization is that MoE + quantization + CPU offload makes frontier-scale models accessible on legacy hardware. You trade speed for accessibility. For research where you need the model's capabilities but not its speed, this works.

Running it as a server (llama-server on port 8080) so I can query it from scripts, notebooks, and a web dashboard.

Code/tools: llama.cpp (CUDA build), batch-probe (PyPI, thermal management), research-portal (PyPI, monitoring dashboard)

Happy to answer setup questions.
