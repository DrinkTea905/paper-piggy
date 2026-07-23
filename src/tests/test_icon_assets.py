import sys
import shutil
import tempfile
import unittest
from pathlib import Path
import re

from PIL import Image


SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))

from icon_utils import ICON_SIZES, ico_is_valid, write_multi_size_ico
import launcher


class IconAssetTests(unittest.TestCase):
    def test_source_png_is_square_rgba_with_transparent_corners(self):
        source = SRC / "web" / "PaperPiggy.png"
        with Image.open(source) as image:
            rgba = image.convert("RGBA")
            self.assertEqual(rgba.width, rgba.height)
            self.assertGreaterEqual(rgba.width, 1024)
            self.assertTrue(all(rgba.getpixel(point)[3] == 0 for point in (
                (0, 0),
                (rgba.width - 1, 0),
                (0, rgba.height - 1),
                (rgba.width - 1, rgba.height - 1),
            )))
            self.assertEqual(rgba.getpixel((rgba.width // 2, rgba.height // 2))[3], 255)

    def test_generated_ico_contains_every_windows_size(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "PaperPiggy.ico"
            write_multi_size_ico(SRC / "web" / "PaperPiggy.png", target)
            self.assertTrue(ico_is_valid(target))
            with Image.open(target) as image:
                self.assertEqual(
                    set(image.info.get("sizes", set())),
                    {(size, size) for size in ICON_SIZES},
                )

    def test_launcher_syncs_cache_and_bundle_root_icons(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "app"
            data = root / "data"
            (app / "web").mkdir(parents=True)
            shutil.copy2(SRC / "web" / "PaperPiggy.png", app / "web" / "PaperPiggy.png")
            (root / "run_localkb.py").write_text("", encoding="utf-8")

            old_app, old_data = launcher.C.APP, launcher.C.DATA
            old_refresh = launcher._refresh_shell_icons
            try:
                launcher.C.APP, launcher.C.DATA = app, data
                launcher._refresh_shell_icons = lambda: None
                result = launcher._ensure_icon()
            finally:
                launcher.C.APP, launcher.C.DATA = old_app, old_data
                launcher._refresh_shell_icons = old_refresh

            self.assertEqual(result, str(data / "PaperPiggy.ico"))
            self.assertTrue(ico_is_valid(data / "PaperPiggy.ico"))
            self.assertTrue(ico_is_valid(root / "PaperPiggy.ico"))

    def test_installer_shortcuts_match_process_app_user_model_id(self):
        launcher_text = (SRC / "launcher.py").read_text(encoding="utf-8")
        installer_text = (SRC.parent / "installer" / "paperpiggy.iss").read_text(encoding="utf-8")
        process_id = re.search(
            r'SetCurrentProcessExplicitAppUserModelID\(\s*"([^"]+)"',
            launcher_text,
        ).group(1)
        shortcut_id = re.search(
            r'#define AppUserModelID "([^"]+)"',
            installer_text,
        ).group(1)
        self.assertEqual(process_id, shortcut_id)
        self.assertEqual(installer_text.count('AppUserModelID: "{#AppUserModelID}"'), 2)


if __name__ == "__main__":
    unittest.main()
