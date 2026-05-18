import os
import sys
import re
import gdown
import requests
from bs4 import BeautifulSoup

def get_google_drive_id(url):
    folder_id = re.search(r'/folders/([0-9A-Za-z_-]{33})', url)
    file_id = re.search(r'/d/([0-9A-Za-z_-]{33})', url)
    
    if folder_id:
        return ('folder', folder_id.group(1))
    elif file_id:
        return ('file', file_id.group(1))
    else:
        raise ValueError("Could not parse Google Drive file or folder ID from the URL.")

def download_file(file_id, destination_path):
    gdown.download(f"https://drive.google.com/uc?export=download&id={file_id}", destination_path, quiet=False)

def download_folder(folder_id, destination_folder):
    url = f"https://drive.google.com/drive/folders/{folder_id}"
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')

    # Extract file URLs and names from the script block
    scripts = soup.find_all('script')
    files = []
    
    for script in scripts:
        if 'window[\'_DRIVE_ivd\']' in script.text:
            file_urls = re.findall(r'https:\\/\\/drive\\.google\\.com\\/file\\/d\\/([0-9A-Za-z_-]{33})', script.text)
            file_names = re.findall(r',\\"([^\"]+)\",\\"(?:text\\/csv|text\\/plain|application\\/vnd\\.google\\-apps\\-document|application\\/pdf|image\\/)', script.text)
            files.extend(zip(file_urls, file_names))

    if not files:
        print("No files found in the folder.")
        return

    if not os.path.exists(destination_folder):
        os.makedirs(destination_folder)

    for file_id, file_name in files:
        # Decode any escape sequences in file_name
        file_name = bytes(file_name, "utf-8").decode("unicode_escape")
        destination_path = os.path.join(destination_folder, file_name)
        download_file(file_id, destination_path)
        print(f"Downloaded {file_name} to {destination_path}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python download_gdrive.py <Google Drive link> <destination path>")
        sys.exit(1)

    gdrive_url = sys.argv[1]
    destination_path = sys.argv[2]

    try:
        item_type, item_id = get_google_drive_id(gdrive_url)

        if item_type == 'file':
            download_file(item_id, destination_path)
            print(f"File downloaded to {destination_path}")
        elif item_type == 'folder':
            download_folder(item_id, destination_path)
    except Exception as e:
        print(f"An error occurred: {e}")

