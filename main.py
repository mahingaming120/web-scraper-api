import os
import io
import asyncio
import zipfile
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import httpx
from fastapi import FastAPI, Form
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# যেকোনো জায়গা থেকে API ব্যবহারের পারমিশন (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

@app.get("/")
def home():
    return {"status": "running", "message": "Scraper API is ready to work!"}

@app.post("/api/scan")
async def scan_website(url: str = Form(...)):
    """ওয়েবসাইট স্ক্যান করে HTML কোড এবং সব ছবির লিংক দেবে"""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url)
            html_content = response.text
            final_url = str(response.url)
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "error": f"সাইট লোড করা যায়নি: {str(e)}"})

    soup = BeautifulSoup(html_content, "html.parser")

    images = []
    seen = set()
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if src:
            full_img_url = urljoin(final_url, src.strip())
            if full_img_url.startswith("http") and full_img_url not in seen:
                seen.add(full_img_url)
                images.append(full_img_url)

    return {
        "success": True,
        "url": final_url,
        "html": html_content,
        "images": images,
        "total_images": len(images)
    }

async def fetch_asset(client, semaphore, url):
    """সার্ভার ওভারলোড ঠেকানোর জন্য সেমাফোর দিয়ে ডাউনলোড"""
    async with semaphore:
        try:
            res = await client.get(url, timeout=10.0)
            if res.status_code == 200:
                return url, res.content
        except Exception:
            return url, None
    return url, None

@app.post("/api/download-zip")
async def download_all_zip(url: str = Form(...)):
    """সবকিছু জিপ ফাইলে বান্ডেল করে ব্রাউজারে পাঠাবে"""
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
            main_res = await client.get(url)
            html_text = main_res.text
            base_url = str(main_res.url)
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"ডাউনলোড সম্ভব হয়নি: {str(e)}"})

    soup = BeautifulSoup(html_text, "html.parser")

    css_urls = [urljoin(base_url, link.get("href")) for link in soup.find_all("link", rel="stylesheet") if link.get("href")]
    js_urls = [urljoin(base_url, script.get("src")) for script in soup.find_all("script") if script.get("src")]
    img_urls = [urljoin(base_url, img.get("src") or img.get("data-src")) for img in soup.find_all("img") if img.get("src") or img.get("data-src")]

    # সার্ভার ক্র্যাশ ঠেকানোর জন্য একসাথে সর্বোচ্চ ১০টি ডাউনলোড
    semaphore = asyncio.Semaphore(10)
    
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        tasks = []
        for u in set(css_urls + js_urls + img_urls):
            if u.startswith("http"):
                tasks.append(fetch_asset(client, semaphore, u))
        
        results = await asyncio.gather(*tasks)

    # মেমোরিতে সরাসরি ZIP তৈরি (ডিস্ক ফুল হবে না)
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr("index.html", html_text)

        for file_url, content in results:
            if not content:
                continue
            path = urlparse(file_url).path
            filename = os.path.basename(path) or "asset.bin"

            if file_url in css_urls:
                zip_file.writestr(f"css/{filename}", content)
            elif file_url in js_urls:
                zip_file.writestr(f"js/{filename}", content)
            else:
                zip_file.writestr(f"images/{filename}", content)

    zip_buffer.seek(0)
    domain_name = urlparse(base_url).netloc.replace(":", "_")
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={domain_name}_source.zip"}
    )
