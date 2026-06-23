import os, re, json, asyncio, random, subprocess
from urllib.parse import quote
import requests, edge_tts
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
VOICE = "tr-TR-AhmetNeural"   # farklılaştırmak istersen: tr-TR-EmelNeural
W, H, FPS = 1280, 720, 25

SYSTEM = """Sen bir belgesel anlatıcısı ve senaristsin. Konu: mantarların
gizli tarihi, mikoloji, miselyum ağları, zehirli ve şifalı türler,
mantarların ekoloji ve insanlık tarihindeki rolü, kültürel efsaneler.

Türkçe, akıcı, merak uyandıran bir dille; liste değil, akan bir hikaye yaz.
Çıktıyı SADECE şu JSON formatında ver, başka HİÇBİR şey yazma:

{
  "baslik": "Merak uyandıran, 60 karakteri geçmeyen başlık",
  "aciklama": "2-3 cümlelik video açıklaması",
  "etiketler": ["mantar","mikoloji","doğa","belgesel","gizli tarih"],
  "segmentler": [
    {"anlatim": "Türkçe anlatım, 2-4 cümle", "gorsel": "detailed English image prompt, cinematic, mycology, forest"},
    ... (toplam 7-9 segment)
  ]
}

Her segmentin 'gorsel' alanı İNGİLİZCE ve görsel olarak zengin olsun.
Görsel stili: scientific illustration meets cinematic forest macro photography,
moody natural light, fungi close-ups — bitki belgeseli estetiğinden FARKLI olsun.
"""

def konu_sec():
    with open("konular.txt", encoding="utf-8") as f:
        konular = [k.strip() for k in f if k.strip()]
    islenmis = set()
    if os.path.exists("islenmis.txt"):
        with open("islenmis.txt", encoding="utf-8") as f:
            islenmis = {k.strip() for k in f if k.strip()}
    for k in konular:
        if k not in islenmis:
            return k
    return None

def json_ayikla(metin):
    metin = re.sub(r"```json|```", "", metin).strip()
    bas, son = metin.find("{"), metin.rfind("}")
    return json.loads(metin[bas:son + 1])

def script_uret(konu):
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": "llama-3.3-70b-versatile",
            "temperature": 0.85,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Konu: {konu}"},
            ],
        },
        timeout=120,
    )
    r.raise_for_status()
    return json_ayikla(r.json()["choices"][0]["message"]["content"])

async def seslendir(metin, dosya):
    await edge_tts.Communicate(metin, VOICE).save(dosya)

def gorsel_indir(prompt, dosya):
    url = (f"https://image.pollinations.ai/prompt/{quote(prompt)}"
           f"?width={W}&height={H}&nologo=true&seed={random.randint(1, 999999)}")
    for deneme in range(3):
        try:
            veri = requests.get(url, timeout=180).content
            if len(veri) > 5000:
                with open(dosya, "wb") as f:
                    f.write(veri)
                return
        except Exception:
            pass
    raise RuntimeError("Görsel indirilemedi: " + prompt[:40])

def sure(ses):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", ses], capture_output=True, text=True).stdout.strip()
    return float(out)

def klip_yap(gorsel, ses, cikti):
    frames = int(sure(ses) * FPS) + FPS
    vf = (f"scale={W*2}:{H*2},"
          f"zoompan=z='min(zoom+0.0005,1.2)':d={frames}:"
          f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},"
          f"format=yuv420p")
    subprocess.run([
        "ffmpeg", "-y", "-loop", "1", "-i", gorsel, "-i", ses,
        "-vf", vf, "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:v", "libx264", "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p", "-shortest", cikti], check=True)

def birlestir(klipler, cikti):
    with open("liste.txt", "w") as f:
        for k in klipler:
            f.write(f"file '{k}'\n")
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "liste.txt",
        "-c:v", "libx264", "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p", cikti], check=True)

def yukle(video, baslik, aciklama, etiketler):
    creds = Credentials(
        None,
        refresh_token=os.environ["YT3_REFRESH_TOKEN"],
        client_id=os.environ["YT3_CLIENT_ID"],
        client_secret=os.environ["YT3_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    yt = build("youtube", "v3", credentials=creds)
    aciklama += "\n\nBu video yapay zekâ destekli olarak üretilmiştir."
    body = {
        "snippet": {"title": baslik[:95], "description": aciklama,
                    "tags": etiketler, "categoryId": "27"},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(video, chunksize=-1, resumable=True)
    resp = yt.videos().insert(part="snippet,status", body=body, media_body=media).execute()
    return resp["id"]

def islendi_kaydet(konu):
    with open("islenmis.txt", "a", encoding="utf-8") as f:
        f.write(konu + "\n")

def main():
    konu = konu_sec()
    if not konu:
        print("İşlenecek konu kalmadı.")
        return
    print("Konu:", konu)

    veri = script_uret(konu)
    klipler = []
    for i, seg in enumerate(veri["segmentler"]):
        ses, gorsel, klip = f"ses_{i}.mp3", f"gorsel_{i}.jpg", f"klip_{i}.mp4"
        asyncio.run(seslendir(seg["anlatim"], ses))
        gorsel_indir(seg["gorsel"], gorsel)
        klip_yap(gorsel, ses, klip)
        klipler.append(klip)

    birlestir(klipler, "video.mp4")
    vid = yukle("video.mp4", veri["baslik"], veri.get("aciklama", ""),
                veri.get("etiketler", []))
    print("Yüklendi: https://youtu.be/" + vid)
    islendi_kaydet(konu)   # ← kritik: ancak başarılı yüklemeden SONRA yazılır

if __name__ == "__main__":
    main()
