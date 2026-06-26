from setuptools import setup, find_packages

setup(
    name="prove-it-ai-gate",
    version="0.1.0",
    description="A lightweight local acceptance gate for AI-generated engineering work",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    packages=find_packages(),
    install_requires=[
        "pyyaml>=6.0",
    ],
    entry_points={
        "console_scripts": [
            "ai-gate=src.cli:main",
        ],
    },
    python_requires=">=3.9",
)
