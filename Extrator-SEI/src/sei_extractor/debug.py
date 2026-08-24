from pathlib import Path
import datetime
import os
import traceback


class DebugDumper:
    def __init__(self, output_dir: str = 'output'):
        self.output_dir = Path(os.environ.get('OUTPUT_DIR', output_dir))
        (self.output_dir / 'debug').mkdir(parents=True, exist_ok=True)

    def dump(self, page, tag: str):
        """Save page/frame HTML and a screenshot to output/debug for inspection.

        page: Playwright Page object (or None)
        tag: short string to identify the dump
        """
        try:
            debug_dir = self.output_dir / 'debug'
            ts = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
            base = debug_dir / f"{tag}_{ts}"

            # top-level HTML
            try:
                with open(base.with_suffix('.page.html'), 'w', encoding='utf-8') as fh:
                    fh.write(page.content() if page else '')
            except Exception:
                pass

            # screenshot
            try:
                if page:
                    page.screenshot(path=str(base.with_suffix('.png')))
            except Exception:
                pass

            # frames
            try:
                if page:
                    for fr in page.frames:
                        name = fr.name or 'frame'
                        safe = name.replace('/', '_') or 'frame'
                        try:
                            with open(debug_dir / f"{tag}_{safe}_{ts}.frame.html", 'w', encoding='utf-8') as fh:
                                fh.write(fr.content())
                        except Exception:
                            pass
            except Exception:
                pass

            # trace (best-effort)
            try:
                with open(base.with_suffix('.trace.txt'), 'w', encoding='utf-8') as fh:
                    fh.write(traceback.format_exc())
            except Exception:
                pass

        except Exception:
            # never raise from debug dumping
            pass
