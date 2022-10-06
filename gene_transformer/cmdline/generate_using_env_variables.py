from argparse import ArgumentParser
from pathlib import Path

from gene_transformer.config import ModelSettings
from gene_transformer.model import DNATransformer
from gene_transformer.utils import (
    LoadDeepSpeedStrategy,
    LoadPTCheckpointStrategy,
    non_redundant_generation,
    seqs_to_fasta,
)
import os
import socket


def main():
    parser = ArgumentParser()
    parser.add_argument("-c", "--config", type=Path, required=True)
    parser.add_argument("-o", "--output_folder", type=Path, required=True)
    parser.add_argument("-n", "--num_seqs", type=int, required=True)
    parser.add_argument("-s", "--name_prefix", type=str, default="SyntheticSeq")
    parser.add_argument(
        "-t",
        "--temperature",
        type=float,
        default=1.0,
        help="Temperature argument to pass to generate",
    )
    parser.add_argument(
        "-k",
        "--known_sequence_files",
        required=False,
        nargs="+",
        help="Space separated list of known sequence files.",
    )
    args = parser.parse_args()

    gpu_number = os.environ.get("SUBNODE_RANK")
    pmi_rank = os.environ.get("PMI_RANK")

    os.mkdirs(args.output_folder, exist_ok=True)

    output_fasta = args.output_folder / "rank{}.fasta".format(pmi_rank)
    seq_name = args.name_prefix + "_{}".format(pmi_rank)

    # Load the model settings file
    config = ModelSettings.from_yaml(args.config)

    # Check to make sure we have a valid checkpoint file to load from
    if config.load_pt_checkpoint is not None:
        load_strategy = LoadPTCheckpointStrategy(
            config.load_pt_checkpoint, cfg=config, generation_flag=True
        )
    elif config.load_ds_checkpoint is not None:
        load_strategy = LoadDeepSpeedStrategy(
            config.load_ds_checkpoint, cfg=config, generation_flag=True
        )
    else:
        raise ValueError(
            "load_ds_checkpoint or load_pt_checkpoint must be set in the config file"
        )

    gpu_number = int(gpu_number)
    if gpu_number not in [0, 1, 2, 3]:
        print(
            gpu_number,
            socket.gethostname(),
        )

    model = load_strategy.get_model(DNATransformer)
    try:
        model.cuda(gpu_number)
    except:
        print(
            "ERROR: ",
            gpu_number,
            socket.gethostname(),
        )
        print("Running on CPU.... don't expect any sequences out of this one.")
    # need to make sure we're in inference mode
    model.eval()

    if args.known_sequence_files is not None:
        for i in args.known_sequence_files:
            print(i)
        print("Using known sequence files: {}".format(args.known_sequence_files))

    # Generate sequences using the model
    try:
        results = non_redundant_generation(
            model.model,
            model.tokenizer,
            num_seqs=args.num_seqs,
            known_sequence_files=args.known_sequence_files,
            start_sequence=None,
            to_stop_codon=False,
            max_length=config.block_size,
            write_to_file=output_fasta,
            custom_seq_name=seq_name,
            temperature=args.temperature,
        )
        unique_seqs, all_seqs = results["unique_seqs"], results["all_generated_seqs"]
        print(f"Proportion of unique seqs: {len(unique_seqs) / len(all_seqs)}")

        # Write fasta with unique sequences to disk
        seqs_to_fasta(unique_seqs, args.output_fasta, custom_seq_name=args.name_prefix)
    except:
        print(
            "Failure generating on {}, rank {}".format(socket.gethostname(), pmi_rank)
        )


if __name__ == "__main__":
    main()
