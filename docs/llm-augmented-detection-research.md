# Research: Tiny LLM-Augmented Commercial Detection

Can a small language model running on the Raspberry Pi 5 improve commercial detection accuracy?

## Executive Summary

A **Whisper tiny.en + keyword classifier** pipeline is the most practical addition, running comfortably on the Pi 5 alongside the existing FFmpeg pipeline. A full LLM (even 0.5B parameters) is feasible but may not provide enough incremental accuracy over simpler text classification to justify the CPU cost. The biggest accuracy gain would come from reducing the **90-100s exit delay** when commercials end — transcript analysis can detect program dialog resuming much faster than waiting for the silence gap timeout.

## Current Detection Weaknesses (What an LLM Could Fix)

1. **Exit delay (~90-100s)**: The timeout-based approach must wait 90s after the last boundary marker before declaring PROGRAM. Transcript analysis could detect program content instantly.
2. **False positives from dialog pauses**: Dramatic silence in program content looks identical to commercial boundary silence at the signal level. Transcript content would disambiguate.
3. **Break 2 detection lag (+45s)**: Sparse boundary markers at -42dB mean slow detection. Transcript-based "call now" / pricing language could trigger faster.

## Approach 1: Whisper + Keyword Scoring (Recommended)

### Architecture

```
HDMI Audio ──► FFmpeg ──► silencedetect/blackdetect (existing)
                │
                └──► Whisper tiny.en ──► transcript chunks
                                              │
                                         Keyword Scorer
                                              │
                                    DetectionSignal (new type)
                                              │
                                    ┌─────────┘
                                    ▼
                            DetectionEngine (existing state machine)
```

### How It Works

1. **Whisper tiny.en** transcribes audio in ~10-15s sliding windows
2. A **keyword/pattern scorer** classifies each window as commercial-like or program-like
3. Classification results feed into the DetectionEngine as a new signal type

### Commercial Speech Patterns (High Confidence)

```
COMMERCIAL indicators:
- Calls to action: "call now", "visit", "order today", "don't wait"
- Pricing: "$XX.99", "free shipping", "save XX%", "limited time"
- Legal: "side effects may include", "results may vary", "terms apply"
- URLs/phones: "dot com", "1-800", "triple-eight"
- Urgency: "act now", "limited supply", "while supplies last"
- Brand repetition: same proper noun 3+ times in 15s

PROGRAM indicators:
- Character dialog: names, emotional exchanges, narrative language
- Narrator voice: "previously on", "coming up", story exposition
- Long continuous speech (>30s same speaker)
```

### Pi 5 Resource Budget

| Component | CPU (4 cores) | RAM |
|-----------|--------------|-----|
| FFmpeg filters (existing) | ~30-50% of 1 core | ~100MB |
| Whisper tiny.en (whisper.cpp) | ~80-100% of 1 core | ~75MB |
| Keyword scorer | <5% (regex matching) | ~10MB |
| Python state machine | <5% | ~50MB |
| **Total** | **~2 of 4 cores** | **~235MB** |

Whisper tiny.en achieves **3-5x real-time** on Pi 5 with whisper.cpp — comfortably keeping up with live audio. The keyword scorer is just regex/string matching on small text chunks, essentially free.

### Advantages

- **Lightweight**: No LLM needed — Whisper (~75MB) + regex is far cheaper than any language model
- **Deterministic**: Keyword patterns are debuggable and tunable via config
- **Fast exit detection**: Can detect program resuming within 10-15s instead of 90s
- **Low false positive risk**: Commercial language is very distinctive

### Limitations

- Whisper tiny.en has ~15-20% word error rate — not all keywords will be caught
- Background music during commercials can degrade transcription
- Novel advertising styles without standard keywords would be missed
- English-only (tiny.en) unless using the multilingual model (slower)

## Approach 2: Whisper + Tiny LLM Classifier

### Architecture

Same as Approach 1, but replace the keyword scorer with a small LLM:

```
Transcript chunk ──► Tiny LLM ──► "commercial" / "program" classification
```

### Candidate Models (Pi 5 Compatible)

| Model | Params | RAM (Q4) | Speed (Pi 5) | Notes |
|-------|--------|----------|--------------|-------|
| **Qwen2.5-0.5B** | 0.5B | ~300MB | ~25-40 tok/s | Best accuracy at this size |
| **SmolLM2-360M** | 360M | ~200MB | ~35-50 tok/s | Outperforms all sub-500M models |
| **TinyLlama-1.1B** | 1.1B | ~700MB | ~12-18 tok/s | Better reasoning, more RAM |
| **Gemma3-1B** | 1B | ~600MB | ~15-20 tok/s | Best throughput efficiency |
| **Phi-2** | 2.7B | ~1.6GB | ~4-7 tok/s | Too slow for real-time |

**Recommended**: Qwen2.5-0.5B Q4_K_M — small enough to fit alongside FFmpeg + Whisper, fast enough to classify a 15s transcript chunk in under 1 second.

### Pi 5 Resource Budget

| Component | CPU | RAM |
|-----------|-----|-----|
| FFmpeg filters | ~30-50% of 1 core | ~100MB |
| Whisper tiny.en | ~80-100% of 1 core | ~75MB |
| Qwen2.5-0.5B (Q4) | ~100% of 1 core (intermittent) | ~300MB |
| Python state machine | <5% | ~50MB |
| **Total** | **~3 of 4 cores** | **~525MB** |

Feasible on 4GB Pi 5 but tight on CPU — the LLM inference competes with Whisper for cores. Would benefit from **8GB model** if also running other services.

### LLM Prompt Design

```
Classify this TV audio transcript as COMMERCIAL or PROGRAM.

COMMERCIAL: advertisements, product promotions, calls to action, pricing,
legal disclaimers, sponsor messages.
PROGRAM: show dialog, narration, story content, news reporting.

Transcript: "{chunk}"

Classification:
```

### Advantages Over Keyword Scoring

- Handles paraphrased/novel commercial language
- Understands context ("this product" in a review show vs. in an ad)
- Could distinguish infomercial-style content from actual programs
- Adapts to different advertising styles without keyword list updates

### Disadvantages

- 3-10x more CPU than keyword matching
- Non-deterministic (LLM may hallucinate or flip classification)
- Requires model download (~300MB-1GB)
- Harder to debug than regex patterns
- Marginal accuracy gain over good keyword patterns for standard TV commercials

## Approach 3: Audio Fingerprinting (Alternative, No LLM)

Not an LLM approach, but worth noting: many commercials repeat. A small fingerprint database of known commercial audio segments could provide instant, high-confidence identification via audio matching.

This is how professional systems (like Shazam for ads) work, but requires building/maintaining a fingerprint database. Out of scope for the current project but could be a future enhancement.

## Recommendation

### Start with Approach 1 (Whisper + Keywords)

1. **Highest value/cost ratio** — Whisper tiny.en is proven on Pi 5, keyword matching is nearly free
2. **Solves the biggest weakness** — exit delay drops from 90s to ~15s
3. **Lowest risk** — deterministic, debuggable, no model hallucination
4. **Easy to upgrade** — can swap keyword scorer for LLM later if needed

### Upgrade to Approach 2 (+ LLM) Only If

- Keyword scoring proves insufficient (too many missed commercials)
- You're willing to use the Pi 5 8GB model for headroom
- You want to handle non-English or non-standard commercial formats

### Implementation Sequence

1. Add Whisper tiny.en transcription running alongside FFmpeg (separate thread)
2. Add a `TRANSCRIPT_COMMERCIAL` / `TRANSCRIPT_PROGRAM` signal type
3. Feed transcript signals into DetectionEngine as supplementary evidence
4. Use transcript-based signals to accelerate COMMERCIAL → PROGRAM transition (override the 90s timeout when transcript clearly shows program content)

## Hardware Impact

For **Approach 1** (Whisper + keywords): The recommended **Pi 5 4GB** is sufficient. No hardware change needed.

For **Approach 2** (Whisper + LLM): Consider upgrading to **Pi 5 8GB** (~$80 vs ~$60) for comfortable headroom. The extra $20 provides ~3.5GB more working memory for the LLM.

## References

- [Whisper on Raspberry Pi 5 performance](https://gektor650.medium.com/audio-transcription-with-openai-whisper-on-raspberry-pi-5-3054c5f75b95)
- [whisper.cpp real-time on Pi](https://github.com/ggml-org/whisper.cpp/discussions/166)
- [LLM benchmarks on Pi 5](https://www.stratosphereips.org/blog/2025/6/5/how-well-do-llms-perform-on-a-raspberry-pi-5)
- [Running 9 LLMs on Pi 5](https://itsfoss.com/llms-for-raspberry-pi/)
- [Small language models survey](https://arxiv.org/html/2501.05465v1)
- [TinyLlama architecture comparison](https://josedavidbaena.com/blog/tiny-language-models/tiny-llm-architecture-comparison)
- [SmolLM benchmark results](https://medium.com/@darrenoberst/best-small-language-models-for-accuracy-and-enterprise-use-cases-benchmark-results-cf71964759c8)
- [Edge AI on Arm CPUs](https://www.edge-ai-vision.com/2025/01/harnessing-the-power-of-llm-models-on-arm-cpus-for-edge-devices/)
- [SLMs for edge deployment](https://blog.premai.io/small-language-models-slms-for-efficient-edge-deployment/)
- [llama.cpp on Pi 5 guide](https://ohyaan.github.io/tips/local_llm_optimization_with_llama.cpp_-_on-device_ai/)
