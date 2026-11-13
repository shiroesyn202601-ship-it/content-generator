"""
Jalankan aplikasi:            python run.py              (auto-reload saat kode/.env berubah)
Tanpa auto-reload:            python run.py --no-reload
Hubungkan Google (sekali):    python run.py --setup-google
Pulihkan proyek dari Drive:   python run.py --restore <drive_file_id_project.json>
"""
import shutil
import sys
import webbrowser

from app import config


def main():
    if "--setup-google" in sys.argv:
        from app.services import google_auth, gdrive, gsheets
        google_auth.interactive_setup()
        print("Folder Drive  :", gdrive.root_folder())
        print("Spreadsheet DB:", f"https://docs.google.com/spreadsheets/d/{gsheets.ensure_schema()}")
        return
    if "--restore" in sys.argv:
        from app.services import sync
        print("Dipulihkan:", sync.restore_from_drive(sys.argv[sys.argv.index("--restore") + 1]))
        return

    if not shutil.which(config.FFMPEG):
        print("PERINGATAN: ffmpeg tidak ditemukan di PATH — render/potong video akan gagal.")
    import uvicorn
    host = "127.0.0.1" if config.HOST in ("0.0.0.0", "::") else config.HOST
    url = f"http://{host}:{config.PORT}"
    print(f"Shiroe Syn Content Studio -> {url}")
    if config.HOST in ("0.0.0.0", "::"):
        print("PERINGATAN: HOST=0.0.0.0 — aplikasi bisa diakses semua perangkat di Wi-Fi ini TANPA login. "
              "Pakai hanya di jaringan rumah.")
    if not config.find_deno():
        print("Info: Deno belum terpasang — unduh video YouTube (Clipper) akan gagal. "
              "Install: winget install DenoLand.Deno")
    webbrowser.open(url)
    # Auto-reload: server restart sendiri tiap ada perubahan kode Python atau .env (matikan: --no-reload).
    # Catatan: job yang sedang berjalan (mis. render) ikut terhenti saat reload terjadi.
    reload = "--no-reload" not in sys.argv
    if reload:
        print("Auto-reload aktif: perubahan *.py / .env langsung dipakai tanpa restart manual.")
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, reload=reload,
                reload_dirs=[str(config.ROOT / "app"), str(config.ROOT)] if reload else None,
                reload_includes=["*.py", ".env"] if reload else None,
                reload_excludes=[".venv/*", "data/*", "web/*", "credentials/*"] if reload else None)


if __name__ == "__main__":
    main()
