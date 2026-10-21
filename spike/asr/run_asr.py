import sys, time, re, resource, os
os.environ.setdefault("HF_HOME", os.path.join(os.path.dirname(os.path.abspath(__file__)), "hf_cache"))
from faster_whisper import WhisperModel
model_name = sys.argv[1]
T = "/Users/emtrade/Emtrade/recording-in-progress/testdata/"
A = "Saya tidak setuju dengan usulan itu. Biaya server akan naik dua kali lipat, dan kita belum punya data pengguna yang cukup untuk membenarkan pengeluaran sebesar itu."
B = "But the cost is justified. Without the new cluster the checkout page will keep timing out during every sale, and customers will simply leave."
C = "I think both of you are missing the point. We should measure first, then decide. Let us run a one week load test before spending anything."
truth = {"mic": (A, "id"), "system": (B + " " + C, "en"), "mixed": (A + " " + B + " " + C, "en")}
def norm(s): return re.findall(r"[a-z0-9']+", s.lower())
def wer(ref, hyp):
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        p, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            p, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, p + (r[i - 1] != h[j - 1]))
    return d[len(h)] / len(r), len(r)
t0 = time.time()
m = WhisperModel(model_name, device="cpu", compute_type="int8")
print(f"MODEL {model_name} LOAD_SEC {time.time()-t0:.1f} maxrss_MB_after_load {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e6:.0f}", flush=True)
for f in ["mic", "system", "mixed"]:
    ref, lang = truth[f]
    for vad in (True, False):
        t = time.time()
        segs, info = m.transcribe(T + f + ".wav", language=lang, word_timestamps=True, vad_filter=vad)
        segs = list(segs)
        dt = time.time() - t
        text = " ".join(s.text.strip() for s in segs)
        w, n = wer(ref, text)
        print(f"\n=== {f}.wav lang={lang} vad={vad} dur={info.duration:.1f}s proc={dt:.1f}s RTF={dt/info.duration:.2f} WER={w:.2f} (ref {n} words)")
        print("TEXT:", text)
        print("WORDS:", " ".join(f"{x.word.strip()}[{x.start:.2f}-{x.end:.2f}]" for s in segs for x in (s.words or [])), flush=True)
print(f"\nPEAK_RSS_MB_total {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e6:.0f}")
