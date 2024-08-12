import gdown
import sys
import re

def get_google_drive_file_id(url):
    file_id = re.search(r'/d/([0-9A-Za-z_-]{33})', url)
    if not file_id:
        raise ValueError("Could not parse Google Drive file ID from the URL.")
    return file_id.group(1)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python download_gdrive.py <Google Drive file link> <destination file path>")
        sys.exit(1)

    file_url = sys.argv[1]
    destination_path = sys.argv[2]

    try:
        file_id = get_google_drive_file_id(file_url)
        gdown.download(f"https://drive.google.com/uc?export=download&id={file_id}", destination_path, quiet=False)
        print(f"File downloaded as {destination_path}")
    except Exception as e:
        print(f"An error occurred: {e}")

