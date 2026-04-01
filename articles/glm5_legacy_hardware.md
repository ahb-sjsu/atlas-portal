# I'm Running a 744B Parameter Model on a 2015 Workstation

GLM-5 has 744 billion parameters. It's designed for datacenter clusters with 8x H100s. I'm running it on an HP Z840 I bought refurbished for $800.

## The Hardware

- HP Z840 (2015, dual-socket Xeon workstation)
- 2x Xeon E5-2690 v3 (48 threads total, 2.60 GHz)
- 224 GB DDR4 ECC RAM
- 2x Quadro GV100 32 GB (Volta, compute 7.0)
- 1.8 TB NVMe + 15 TB RAID-5 archive
- Ubuntu 24.04, CUDA 12.8

Total cost including the GPUs: under $5,000. A single H100 costs $30,000.

## How It Works

GLM-5 is a Mixture-of-Experts model: 744B total parameters, but only 40B are active per token. The GGUF quantized model (Q3_K_M, 50% REAP pruning) is 170 GB on disk.

The trick is layer splitting:
- 25 of 80 layers offloaded to the two GPUs (layer-split mode)
- Remaining 55 layers run on CPU from RAM
- Each GPU holds ~25-31 GB of layer weights
- The 224 GB RAM holds the full model plus KV cache

**Result: 1.54 tokens/second.** Not fast. But functional.

For comparison, that's about 90 words per minute of output — roughly the speed of a human typing. For batch tasks like code generation, paper summarization, or research Q&A, you fire off a prompt and get a response in 30-60 seconds. Perfectly usable.

## The Key Insight: MoE Changes Everything

Dense 744B models would need ~370 GB of VRAM at FP16. That's impossible on consumer hardware. But GLM-5's MoE architecture means each token only touches 40B parameters. The inactive experts sit cold in RAM until needed.

The bottleneck shifts from GPU compute to RAM bandwidth. DDR4 on the Z840 delivers ~50 GB/s. Each token loads ~20 GB of active expert weights from RAM. That gives a theoretical ceiling of ~2.5 tok/s, and we achieve 1.54 — about 60% efficiency.

## What I Had to Figure Out

**GPU memory allocation:** 25 GPU layers across 2 GPUs with a 40/60 split. Too many layers and you OOM. Too few and you waste GPU. I wrote a binary search using batch-probe to find the maximum that fits.

**Layer split, not row split:** Without NVLink peer-to-peer (the Z840's PCIe topology doesn't support it — the two GPU slots are on different CPU sockets), row-split leaves GPU 1 idle. Layer-split assigns whole layers to each GPU, so both participate in the forward pass.

**Thermal management:** 48 CPU cores at full utilization push Package 0 to 84C. batch-probe's ThermalController (Kalman-filtered PI controller) throttles thread count to keep temps under 82C.

**The NVLink bridge I bought for $80 doesn't work** on this motherboard. The Z840 routes its PCIe slots to different CPU sockets, and NVLink can't bridge across the QPI interconnect. I verified this with `nvidia-smi topo -p2p r` (TNS: Topology Not Supported) and tried every kernel parameter including `iommu=off`, `pci=noacs`, and `pcie_acs_override=downstream,multifunction`. None worked. Check your PCIe topology before buying a bridge.

## What It's Actually Good For

At 1.54 tok/s, interactive chat is painful. But:

- **Code generation:** Send a prompt, get 500 tokens back in 5 minutes. I'm using it for ARC-AGI-2 puzzle solving.
- **Research Q&A:** With RAG over my paper collection, it answers questions about my own research grounded in my actual data.
- **Paper review:** Feed it a draft, get structured feedback. The 4K context window handles ~6 pages of text.
- **Batch processing:** Queue up 50 prompts overnight, have results in the morning.

## The Cost Comparison

| Setup | Hardware Cost | GLM-5 Speed | Monthly Cloud Equivalent |
|-------|-------------|-------------|------------------------|
| My Z840 | $5,000 (one-time) | 1.54 tok/s | ~$2,000/mo on A100 instances |
| 8x H100 node | $250,000+ | ~100 tok/s | N/A (you own it) |
| API (GLM-5 cloud) | $0 | ~50 tok/s | ~$500/mo at research volumes |

The Z840 pays for itself in 2.5 months vs. cloud API pricing, and you own the hardware forever. You also get full data privacy — nothing leaves your machine.

## What I'd Do Differently

1. **Buy DDR4 ECC in matched sets.** I mixed DIMM types and got 224 GB instead of 384 GB. With 384 GB I could fit more layers in RAM and reduce GPU↔RAM transfers.

2. **Check PCIe topology BEFORE buying NVLink.** One `lspci -tv` command would have saved me $80 and two hours of debugging.

3. **Start with llama.cpp's `--split-mode layer`, not `row`.** Row mode requires working P2P. Layer mode works everywhere.

4. **Use batch-probe for GPU layer probing.** Binary search beats guessing, and the thermal controller prevents overheating during long inference sessions.

## The Stack

- **llama.cpp** (built with CUDA) for inference
- **batch-probe** (PyPI) for GPU memory probing and thermal management
- **research-portal** (PyPI) for monitoring — shows GPU utilization, CPU temps, active experiments
- **GGUF Q3_K_M with 50% REAP pruning** for the model format — balances quality and size

All open source. Total software cost: $0.

---

The era of "you need a datacenter" for frontier models is ending. MoE architectures, aggressive quantization, and smart memory management put 744B models within reach of hardware you can buy on eBay. It won't be fast, but it works.

GitHub: https://github.com/ahb-sjsu
PyPI: `pip install research-portal` / `pip install batch-probe`

#MachineLearning #LLM #GPU #OpenSource #GLM5 #HPC #LocalAI
