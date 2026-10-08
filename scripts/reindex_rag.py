"""Rebuild / refresh the RAG index from the command line (run from the project root).

    python -m scripts.reindex_rag              # text: incremental (changed docs / newly-judged alerts)
    python -m scripts.reindex_rag --full       # text: wipe + rebuild (after changing RAG_EMBED_MODEL)
    python -m scripts.reindex_rag --images     # snapshots: index every alert snapshot not yet in CLIP index
    python -m scripts.reindex_rag --images-full  # snapshots: wipe + rebuild the CLIP index

Changing RAG_EMBED_MODEL does NOT require an image rebuild (CLIP is independent of it).
"""
import argparse
import asyncio
import json

from backend.rag import image_index, ingest


async def _run(args) -> dict:
    out = {}
    if args.images or args.images_full:
        if args.images_full:
            await image_index.reset_index()
        out["images_indexed"] = await image_index.backfill()
        out["images_total"] = image_index.count()
    else:
        out = await ingest.reindex(full=args.full)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--full", action="store_true")
    p.add_argument("--images", action="store_true")
    p.add_argument("--images-full", action="store_true")
    print(json.dumps(asyncio.run(_run(p.parse_args())), indent=2, default=str))


if __name__ == "__main__":
    main()