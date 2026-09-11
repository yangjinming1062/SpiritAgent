#!/usr/bin/env python3
import argparse
import asyncio
import base64
import json
import os
import sys
from pathlib import Path

from openai import AsyncOpenAI

# 请求体结构对齐 backend/services/llm/providers/mimo/tts.py::synthesize()，保证同一音色下产出音频与 /api/media/tts 运行时字节一致。用法见 scripts/onboarding-audio/README.md。

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST_PATH = REPO_ROOT / "scripts" / "onboarding-audio" / "manifest.json"
OUTPUT_DIR = REPO_ROOT / "installer" / "payload" / "onboarding-audio" / "zh"

MIMO_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"
MIMO_TTS_MODEL = "mimo-v2.5-tts"
# 限制并发请求数，避免上游降速时触发 429。
SYNC_CONCURRENCY = 10


def _validate_mp3_sync(path: Path) -> bool:
    # MPEG 音频帧同步字：0xFF 后跟 0xFB / 0xFA / 0xF3 / 0xF2。
    head = path.read_bytes()[:4]
    return len(head) >= 2 and head[0] == 0xFF and head[1] in (0xFB, 0xFA, 0xF3, 0xF2)


async def _synthesize_one(client: AsyncOpenAI, voice: str, text: str) -> bytes:
    response = await client.chat.completions.create(
        model=MIMO_TTS_MODEL,
        messages=[
            {"role": "user", "content": ""},
            {"role": "assistant", "content": text},
        ],
        audio={"format": "mp3", "voice": voice},
    )
    choice = response.choices[0] if response.choices else None
    if not choice or not getattr(choice.message, "audio", None):
        raise RuntimeError("MiMo TTS returned no audio")
    return base64.b64decode(choice.message.audio.data)


async def _generate(manifest: dict, output_dir: Path) -> int:
    voice = manifest["voice"]
    files = manifest["files"]
    output_dir.mkdir(parents=True, exist_ok=True)

    api_key = os.environ.get("MIMO_API_KEY") or os.environ.get("TTS_API_KEY")
    if not api_key:
        raise SystemExit("Set MIMO_API_KEY (or TTS_API_KEY) before running.")

    client = AsyncOpenAI(api_key=api_key, base_url=MIMO_BASE_URL)

    async def one(entry: dict) -> Path:
        audio = await _synthesize_one(client, voice, entry["text"])
        out_path = output_dir / f"{entry['tag']}.mp3"
        out_path.write_bytes(audio)
        return out_path

    sem = asyncio.Semaphore(SYNC_CONCURRENCY)

    async def bounded(entry: dict) -> Path:
        async with sem:
            return await one(entry)

    results = await asyncio.gather(*(bounded(e) for e in files))
    for path in results:
        print(f"  wrote {path.relative_to(REPO_ROOT)} ({path.stat().st_size} bytes)")

    await client.close()
    return len(results)


def _check(manifest: dict, output_dir: Path) -> int:
    expected_tags = {entry["tag"] for entry in manifest["files"]}
    if not output_dir.is_dir():
        print(f"missing output dir: {output_dir}", file=sys.stderr)
        return 1

    actual_tags: set[str] = set()
    bad: list[Path] = []
    for p in output_dir.glob("*.mp3"):
        actual_tags.add(p.stem)
        if not _validate_mp3_sync(p):
            bad.append(p)

    missing = expected_tags - actual_tags
    extras = actual_tags - expected_tags
    # 同步字检查只在文件齐全时有意义；缺失文件已经独立判失败。
    if missing:
        bad = []

    if missing:
        print(f"missing files: {sorted(missing)}", file=sys.stderr)
    if extras:
        print(f"unexpected files: {sorted(extras)}", file=sys.stderr)
    if bad:
        print(f"files failing mp3 sync check: {sorted(p.name for p in bad)}", file=sys.stderr)
    return 1 if (missing or extras or bad) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify manifest + mp3 sync bytes; do not regenerate.")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    files = manifest["files"]
    print(f"manifest: {len(files)} entries, voice={manifest['voice']!r}, lang={manifest['language']}")

    if args.check:
        return _check(manifest, args.output_dir)

    written = asyncio.run(_generate(manifest, args.output_dir))
    print(f"done: {written} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
