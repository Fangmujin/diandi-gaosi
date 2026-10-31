"""通过 run-as 向应用私有目录注入 fixture 项目，用于 UI 验证。"""
import json
import os
import subprocess
import tempfile

ROOT = "C:/Users/28928/WorkBuddy/2026-10-11-15-32-41/diandi-gaosi"
ADB = "C:/Users/28928/AppData/Local/Android/Sdk/platform-tools/adb.exe"


def adb_shell(cmd):
    p = subprocess.run([ADB, "shell", cmd], capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        print("adb shell failed:", p.returncode, p.stderr)
    return p


def adb_push(local, remote):
    p = subprocess.run([ADB, "push", local, remote], capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        print("adb push failed:", p.stderr)
    else:
        print("adb push ok", remote)
    return p


def push_text_to_file(text, rel_path, tmp_local):
    with open(tmp_local, "w", encoding="utf-8") as f:
        f.write(text)
    remote_tmp = "/data/local/tmp/tmpseed.txt"
    adb_push(tmp_local, remote_tmp)
    adb_shell("run-as com.diandi.gaosi sh -c 'cat /data/local/tmp/tmpseed.txt > files/projects/" + rel_path + "'")


def push_binary(local, rel_path):
    remote_tmp = "/data/local/tmp/tmpseed.bin"
    adb_push(local, remote_tmp)
    adb_shell("run-as com.diandi.gaosi sh -c 'cat /data/local/tmp/tmpseed.bin > files/projects/" + rel_path + "'")


def main():
    # 查看页验证用真实高斯：DIANDI_SEED_PLY 指向真机重建产出的 .ply（默认退回合成 demo.ply）
    seed_ply = os.environ.get("DIANDI_SEED_PLY") or os.path.join(ROOT, "build", "demo.ply")
    seed_ply = seed_ply.replace("\\", "/")
    if not os.path.exists(seed_ply):
        print("seed ply missing:", seed_ply)
        return
    # 从 PLY header 读真实点数，UI 上的"高斯点数"不能是编的
    splats = 0
    with open(seed_ply, "rb") as f:
        head = f.read(4096)
    import re
    m = re.search(rb"element\s+vertex\s+(\d+)", head)
    if m:
        splats = int(m.group(1))
    print("seed ply:", seed_ply, "splats:", splats)

    demo = {
        "id": "demo",
        "name": "示例模型",
        "quality": "fast",
        "status": "done",
        "progress": 100,
        "stage": "已完成",
        "createdAt": 1728880000000,
        "date": "2026-10-14 10:00",
        "splats": splats,
        "bytes": os.path.getsize(seed_ply),
        "frames": 1,
    }
    fail1 = {
        "id": "fail1",
        "name": "失败示例",
        "quality": "fast",
        "status": "failed",
        "progress": 0,
        "stage": "重建",
        "createdAt": 1728876400000,
        "date": "2026-10-14 09:00",
        "splats": 0,
        "bytes": 0,
        "frames": 0,
        "error": "重建引擎未就绪",
        "sourceRel": "nonexistent.mp4",
    }
    index = [demo, fail1]

    tmpdir = tempfile.mkdtemp(prefix="diandi_seed_")

    # getFilesDir() = <datadir>/files，不是 filesDir
    adb_shell("run-as com.diandi.gaosi rm -rf filesDir")
    adb_shell("run-as com.diandi.gaosi mkdir -p files/projects/demo/frames")
    adb_shell("run-as com.diandi.gaosi mkdir -p files/projects/fail1/frames")
    adb_shell("run-as com.diandi.gaosi mkdir -p files/imports")

    push_text_to_file(json.dumps(demo, ensure_ascii=False), "demo/meta.json", os.path.join(tmpdir, "demo_meta.json"))
    push_text_to_file(json.dumps(fail1, ensure_ascii=False), "fail1/meta.json", os.path.join(tmpdir, "fail1_meta.json"))
    push_text_to_file(json.dumps(index, ensure_ascii=False), "index.json", os.path.join(tmpdir, "index.json"))
    push_binary(seed_ply, "demo/final.ply")

    # 一个占位素材，用于让「开始重建」能真实启动一次管道（起飞前体检会命中引擎缺失）
    push_binary(os.path.join(ROOT, "build", "demo.ply"), "../imports/src_seed.mp4")

    adb_shell("rm -f /data/local/tmp/tmpseed.txt /data/local/tmp/tmpseed.bin")
    p = adb_shell("run-as com.diandi.gaosi ls -R files")
    print(p.stdout)


if __name__ == "__main__":
    main()
