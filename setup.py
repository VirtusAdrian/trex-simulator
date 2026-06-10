from setuptools import setup, find_packages

setup(
    name="trex-simulator",
    version="1.0.0",
    description="基于 Cisco TRex 的 TCP 多核打流模拟器",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "click>=8.0",
        "paramiko>=3.0",
    ],
    entry_points={
        "console_scripts": [
            "trex-sim=trex_sim.cli:main",
        ],
    },
)
