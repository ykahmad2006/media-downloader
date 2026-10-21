import requests

url = input("Enter the authorized video URL: ").strip()
filename = input("Save as (example: episode.mp4): ").strip()

headers = {
    "User-Agent": "Mozilla/5.0"
}

with requests.get(url, headers=headers, stream=True, timeout=60) as response:
    response.raise_for_status()

    total = int(response.headers.get("content-length", 0))
    downloaded = 0

    with open(filename, "wb") as file:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue

            file.write(chunk)
            downloaded += len(chunk)

            if total:
                percent = downloaded / total * 100
                print(f"\rDownloading: {percent:.1f}%", end="")

print("\nDownload complete.")