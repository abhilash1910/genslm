import torch
from torch.utils.data import Dataset
from Bio import SeqIO  # type: ignore[import]
from transformers import PreTrainedTokenizerFast
import numpy as np
import pickle
import os
from glob import glob
from natsort import natsorted
from pathlib import Path
import pdb
from tqdm import tqdm
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
from mpire import WorkerPool
from mpire.utils import make_single_arguments
from tqdm import tqdm
import h5py
from functools import partial
import pdb


def group_with_spacing(s: SeqIO.SeqRecord, n: int) -> str:
    seq = str(s.seq)
    return " ".join(seq[i: i + n] for i in range(0, len(seq), n))


class IndividualFastaDataset(Dataset):
    def __init__(
            self, dir_path: str, block_size: int, tokenizer: PreTrainedTokenizerFast, spacing: int = 3, njobs: int = 20
    ):
        print("Individual fasta dataset")
        self.dir_path = dir_path
        self.block_size = block_size
        self.tokenizer = tokenizer
        self.spacing = spacing
        self.njobs = njobs

        ls_path = Path(self.dir_path) / "*.fasta"
        self.files = natsorted(glob(str(ls_path)))

        self.pad_sequence = partial(
            torch.nn.functional.pad, value=tokenizer.pad_token_id
        )

        # initialize reading from fasta files
        # self.samples = {}

        # def _single_encode(fasta_file):
        #     sequence = list(SeqIO.parse(fasta_file, "fasta"))[0]
        #     return self.tokenizer.encode(
        #         group_with_spacing(sequence, self.spacing),
        #         # return_tensors="pt",
        #         max_length=self.block_size,
        #         padding="max_length",
        #     )
        #
        # def tokenize_samples(seqs):
        #     print("Tokenizing samples...")
        #     with WorkerPool(n_jobs=self.njobs) as pool:
        #         results = pool.map(_single_encode, make_single_arguments(self.files), progress_bar=True,
        #                            iterable_len=len(seqs))
        #     return torch.tensor(results)
        #
        # self.samples = tokenize_samples(self.files)

    def __len__(self) -> int:
        return len(self.files)

    def __getitem__(self, idx: int) -> torch.Tensor:
        # tokenize on the fly
        # return self.samples[idx].long()
        sequence = list(SeqIO.parse(self.files[idx], "fasta"))[0]
        pdb.set_trace()
        encoded_sequence = torch.tensor(self.tokenizer.encode(
                group_with_spacing(sequence, self.spacing),
                # return_tensors="pt",
                max_length=self.block_size,
                padding="max_length",
            ))
        print(encoded_sequence)
        return encoded_sequence


class H5Dataset(Dataset):
    def __init__(
            self,
            file_path: str,
            dset_name: str,
            block_size: int,
            tokenizer: PreTrainedTokenizerFast,
    ) -> None:
        self.file_path = file_path
        self.dset_name = dset_name
        self.block_size = block_size
        # self.tokenizer = tokenizer

        with h5py.File(file_path, "r") as f:
            # fetch all samples from the dataset
            self.samples = f[self.dset_name][...]

        # define padding function
        self.pad_sequence = partial(
            torch.nn.functional.pad, value=tokenizer.pad_token_id
        )

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> torch.Tensor:
        item = torch.tensor(self.samples[idx].astype("int32")).long()
        # if len(item) < self.block_size:
        item = self.pad_sequence(
            item,
            (0, self.block_size - len(item)),
        )
        return item  # type:ignore[no-any-return]


class BPEGenomeDataset(Dataset):
    def __init__(
            self, samples_path: str, block_size: int, tokenizer: PreTrainedTokenizerFast
    ) -> None:
        """PyTorch Dataset that tokenizes genome sequences using byte pair encoding tokenizer

        Parameters
        ----------
        fasta_file : str
            Path to fasta file to read sequence from.
        block_size : int
            max_length of :obj:`tokenizer` encoder.
        tokenizer : PreTrainedTokenizerFast
            Converts raw strings to tokenized tensors.
        """

        self.block_size = block_size
        self.tokenizer = tokenizer

        # with open(fasta_file, "r") as f:
        #     fasta_string = f.read()
        #
        # # not returning pt tensor, it messes with the batching/block size
        # self.batch_encode_output = tokenizer(
        #     fasta_string, max_length=block_size, return_overflowing_tokens=True
        # )

        # check if we're working with a single pickle file or a directory of them

        print("Processing {}...".format(samples_path))

        if os.path.isfile(samples_path):
            with open(samples_path, "rb") as f:
                self.samples = pickle.load(f)

        else:
            self.samples = []
            ls_path = Path(samples_path) / "*.pkl"
            files = natsorted(glob(str(ls_path)))
            for path in tqdm(files):
                with open(path, "rb") as f:
                    sample_section = pickle.load(f)
                self.samples.extend(sample_section)

    def __len__(self) -> int:
        # return len(self.batch_encode_output.input_ids)
        return len(self.samples)

    def __getitem__(self, idx: int) -> torch.Tensor:
        item = torch.tensor(self.samples[idx])
        # item = torch.tensor(self.batch_encode_output.input_ids[idx])
        if len(item) < self.block_size:
            item = torch.nn.functional.pad(
                item,
                (0, self.block_size - len(item)),
                value=self.tokenizer.pad_token_id,
            )
        elif len(item) > self.block_size:
            raise ValueError(
                "Length of encoded block is greater than set block size, something is very wrong."
            )

        return item


class GenomeDataset(Dataset):
    def __init__(
            self, fasta_file: str, block_size: int, tokenizer: PreTrainedTokenizerFast
    ) -> None:
        """PyTorch Dataset that tokenizes sequences by codon.

        Parameters
        ----------
        fasta_file : str
            Path to fasta file to read sequence from.
        block_size : int
            max_length of :obj:`tokenizer` encoder.
        tokenizer : PreTrainedTokenizerFast
            Converts raw strings to tokenized tensors.
        """

        seq_records = list(SeqIO.parse(fasta_file, "fasta"))
        self.tokenized_sequences = []
        for s in seq_records:
            self.tokenized_sequences.extend(
                self.create_token_set_from_record(
                    s, tokenizer=tokenizer, block_size=block_size
                )
            )

    def create_token_set_from_record(self, s, tokenizer, block_size=512):
        sequence = str(s.seq.upper())
        sequence = " ".join(sequence[i: i + 3] for i in range(0, len(sequence), 3))
        sequence = "[START] " + sequence + " [END]"
        out = tokenizer.encode(
            sequence, max_length=block_size, return_overflowing_tokens=True
        )
        if len(out[-1]) != block_size:
            padded_last_chunk = list(
                np.pad(
                    out[-1],
                    (0, block_size - len(out[-1])),
                    mode="constant",
                    constant_values=tokenizer.vocab["[PAD]"],
                )
            )
            out = out[:-1]
            out.append(padded_last_chunk)
        return out

    def __len__(self) -> int:
        return len(self.tokenized_sequences)

    def __getitem__(self, idx: int) -> torch.Tensor:
        return torch.tensor(self.tokenized_sequences[idx])  # type:ignore[no-any-return]


class FASTADataset(Dataset):  # type: ignore[type-arg]
    def __init__(
            self,
            fasta_file: str,
            block_size: int,
            tokenizer: PreTrainedTokenizerFast,
            alphabet: str = "codon",
            kmer_size: int = 3,
    ) -> None:
        """PyTorch Dataset that tokenizes sequences by codon.

        Parameters
        ----------
        fasta_file : str
            Path to fasta file to read sequence from.
        block_size : int
            max_length of :obj:`tokenizer` encoder.
        tokenizer : PreTrainedTokenizerFast
            Converts raw strings to tokenized tensors.
        """

        self.kmer_size = kmer_size

        def _single_encode(sequence):
            return tokenizer.encode(
                group_with_spacing(sequence, self.kmer_size),
                # return_tensors="pt", # currently not returning torch tensors since it causes memory issues
                max_length=block_size,
                padding="max_length",
            )

        # Read in the sequences from the fasta file, convert to
        # codon string, tokenize, and collect in tensor
        print("Processing {}...".format(fasta_file))
        parsed_seqs = list(SeqIO.parse(fasta_file, "fasta"))
        num_seqs = len(parsed_seqs)
        samples = []
        for chunk in tqdm(list(chunks(parsed_seqs, 50000))):
            with WorkerPool(n_jobs=4) as pool:
                results = pool.map(
                    _single_encode,
                    make_single_arguments(
                        chunk
                    ),  # need make_single_arguments otherwise map unpacks the seqs
                    progress_bar=False,
                    iterable_len=50000,
                )
                samples.extend(results)
        self.sequences = torch.Tensor(samples)
        print("Encoded all sequences.")

        # pool = multiprocessing.Pool(processes=16)
        # results = pool.map(_single_encode, tqdm(list(SeqIO.parse(fasta_file, "fasta"))))
        # pool.close()
        # pool.join()
        # self.sequences = torch.cat(results)

        # with ProcessPoolExecutor(max_workers=16) as executor:
        #     parsed_seqs = list(SeqIO.parse(fasta_file, "fasta"))
        #     futures = [executor.submit(_single_encode, seq) for seq in tqdm(parsed_seqs)]
        #     results = []
        #     for future in as_completed(tqdm(futures)):
        #         # get the result for the next completed task
        #         result = future.result()  # blocks
        #         print(result)
        #         results.append(result)
        #     # futures = list(tqdm(executor.map(_single_encode, parsed_seqs), total=len(parsed_seqs)))
        #     # results = [f.result() for f in futures]
        #     print("Finished the futures.")
        #     self.sequences = torch.cat(results)
        # print("Completed.")
        # self.sequences = torch.cat(  # type: ignore[attr-defined]
        #     [
        #         tokenizer.encode(
        #             grouping(seq),
        #             return_tensors="pt",
        #             max_length=block_size,
        #             padding="max_length",
        #         )
        #         for seq in tqdm(list(SeqIO.parse(fasta_file, "fasta")))
        #     ]
        # )

    def group_by_codon(self, s: SeqIO.SeqRecord) -> str:
        """Split SeqRecord by codons, return as a string with whitespace.
        eg. 'AAACCC' -> 'AAA CCC'"""
        seq = str(s.seq)
        return " ".join(seq[i: i + 3] for i in range(0, len(seq), 3))

    def group_by_aa(self, s: SeqIO.SeqRecord) -> str:
        seq = str(s.seq).upper()
        return " ".join(i for i in seq)

    def __len__(self) -> int:
        return len(self.sequences)

    def __getitem__(self, idx: int) -> torch.Tensor:
        return self.sequences[idx].long()  # type:ignore[no-any-return]


def chunks(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i: i + n]
