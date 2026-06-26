from setuptools import setup, find_packages

setup(
    name="prove-it-ai-gate",
    version="0.3.1",
    description="A lightweight local CLI acceptance gate for AI-generated engineering work",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    author="Hrishikesh Thakre",
    url="https://github.com/hrishikesh-thakre/prove-it-ai-gate",
    license="MIT",
    packages=find_packages(include=["prove_it_ai_gate", "prove_it_ai_gate.*"]),
    package_data={
        "prove_it_ai_gate": ["policies/*.yml"],
    },
    install_requires=[
        "pyyaml>=6.0",
    ],
    entry_points={
        "console_scripts": [
            "ai-gate=prove_it_ai_gate.cli:main",
        ],
    },
    python_requires=">=3.9",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Quality Assurance",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
)
