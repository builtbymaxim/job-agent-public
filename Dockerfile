# job_agent runtime: Python + XeLaTeX + a portable Helvetica-metric font.
# Personal data (profile, assets, .env, output) is mounted at runtime, never baked in.
FROM python:3.12-slim

# System deps: XeLaTeX + the LaTeX packages the templates use (tikz, fontawesome5,
# polyglossia/german), a Helvetica-metric font registered with fontconfig, and a
# small editor for the [e]dit flow, and poppler-utils for scripts/check_layout.py.
RUN apt-get update && apt-get install -y --no-install-recommends \
        texlive-xetex \
        texlive-latex-recommended \
        texlive-latex-extra \
        texlive-fonts-recommended \
        texlive-fonts-extra \
        texlive-lang-german \
        texlive-pictures \
        fonts-texgyre \
        fontconfig \
        poppler-utils \
        nano \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code only. Profile / assets / .env / applications are mounted at runtime
# (see docker-compose.yml) and excluded from the image via .dockerignore.
COPY agents/ ./agents/
COPY templates/ ./templates/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY *.py config.yaml ./

# Non-macOS main font + an editor for [e]dit; unbuffered logs.
ENV JOB_AGENT_FONT="TeX Gyre Heros" \
    EDITOR=nano \
    PYTHONUNBUFFERED=1

# Runs Python as the owner of the mounted /app (see docker/entrypoint.py).
# `docker run <img>`            -> python run.py
# `docker run <img> run.py --auto` / `... run_tracker.py` -> override the command
COPY docker/entrypoint.py /usr/local/bin/entrypoint.py
ENTRYPOINT ["python", "/usr/local/bin/entrypoint.py"]
CMD ["run.py"]
