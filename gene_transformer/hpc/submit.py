import subprocess
import sys
from argparse import ArgumentParser
from pathlib import Path

import jinja2
from pydantic import BaseModel, validator

import gene_transformer


class HPCSettings(BaseModel):
    allocation: str
    queue: str
    time: str
    nodes: int
    job_name: str
    workdir: Path
    config: Path
    gene_transformer_path: Path = Path(gene_transformer.__file__).parent

    @validator("config")
    def config_exists(cls, v: Path) -> Path:
        if not v.exists():
            raise FileNotFoundError(f"Config file does not exist: {v}")
        return v.resolve()

    @validator("workdir")
    def workdir_exists(cls, v: Path) -> Path:
        v = v.resolve()
        v.mkdir(exist_ok=True, parents=True)
        return v


def format_and_submit(template_name: str, settings: HPCSettings) -> None:
    """Add settings to a submit script and submit to HPC scheduler"""

    env = jinja2.Environment(
        loader=jinja2.PackageLoader("gene_transformer.hpc"),
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
    )

    try:
        template = env.get_template(template_name + ".j2")
    except jinja2.exceptions.TemplateNotFound:
        raise ValueError(f"template {template_name} not found.")

    submit_script = template.render(settings.dict())

    launchers = {"perlmutter": "sbatch", "polaris": "qsub"}
    suffixs = {"perlmutter": "slurm", "polaris": "pbs"}

    sbatch_script = settings.workdir / f"{settings.job_name}.{suffixs[template_name]}"
    with open(sbatch_script, "w") as f:
        f.write(submit_script)

    subprocess.run(f"{launchers[template_name]} {sbatch_script}".split())


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("-T", "--template", default="perlmutter")
    parser.add_argument("-a", "--allocation", default="m3957_g")
    parser.add_argument("-q", "--queue", default="regular")
    parser.add_argument("-t", "--time", default="01:00:00")
    parser.add_argument("-n", "--nodes", default=1, type=int)
    parser.add_argument("-j", "--job_name", default="gene_transformer")
    parser.add_argument("-w", "--workdir", default=Path("."), type=Path)
    parser.add_argument("-c", "--config", required=True, type=Path)
    args = parser.parse_args()

    settings = HPCSettings(
        allocation=args.allocation,
        queue=args.queue,
        time=args.time,
        nodes=args.nodes,
        job_name=args.job_name,
        workdir=args.workdir,
        config=args.config,
    )

    # Log command for reproducibility
    with open("command.log", "w") as f:
        f.write(" ".join(sys.argv))

    # TODO: Log the nodelist

    format_and_submit(args.template, settings)
