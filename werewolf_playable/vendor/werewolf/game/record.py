"""
record.py - 对局记录（jsonl）读写
==================================
一行一局，UTF-8，不转义中文（ensure_ascii=False），方便直接 grep。
格式定义见 docs/对局记录格式.md。

断电续跑：JsonlWriter 以 append 方式打开，每局 flush；
续跑时先读已有文件的 seed 集合，跳过已跑过的 seed。
"""
import json
import os


def dumps(rec):
    return json.dumps(rec, ensure_ascii=False, separators=(",", ":"))


class JsonlWriter(object):
    def __init__(self, path, resume=True):
        self.path = path
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        self.done_seeds = set()
        if resume and os.path.exists(path):
            for rec in read_jsonl(path):
                self.done_seeds.add(rec.get("seed"))
        mode = "a" if (resume and os.path.exists(path)) else "w"
        self.fh = open(path, mode, encoding="utf-8", newline="\n")
        self.count = len(self.done_seeds)

    def write(self, rec):
        self.fh.write(dumps(rec) + "\n")
        self.fh.flush()          # 宿舍断电规则：每局落盘
        self.count += 1
        self.done_seeds.add(rec.get("seed"))

    def close(self):
        if self.fh and not self.fh.closed:
            self.fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def read_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def iter_jsonl(path):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
