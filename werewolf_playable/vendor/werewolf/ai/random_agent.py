"""
random_agent.py - baseline 1：全随机
=====================================
每个决策点在合法动作集上**均匀**随机（含 SILENCE / 弃票 / 不开枪）。
用途：环境的最低标尺 + smoke 压力测试。
"""
from .base import BaseAgent


class RandomAgent(BaseAgent):
    name = "random"

    def act(self, view, legal):
        return self.pick(legal)
