# Copyright (c) Facebook, Inc. and its affiliates.

# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import argparse
import pathlib
import random
from typing import Union

import numpy as np
import torch
from torchvision import datasets

from egg.core.batch import Batch
from egg.core.interaction import Interaction
from egg.zoo.population_game.data import ImageTransformation
from egg.zoo.population_game.games import build_game


def add_common_cli_args(parser):
    parser.add_argument("--batch_size", type=int, default=128)
    parser.add_argument(
        "--dataset_name",
        choices=["cifar10", "imagenet"],
        default="imagenet",
        help="Dataset used for evaluating a trained a model",
    )
    parser.add_argument(
        "--n_senders", type=int, default=3, help="Number of senders in the population"
    )
    parser.add_argument(
        "--n_recvs", type=int, default=3, help="Number of receivers in the population"
    )
    ###
    parser.add_argument("--checkpoint_path", type=str, help="Path to model checkpoint")
    parser.add_argument(
        "--dump_interaction_folder",
        type=str,
        default=None,
        help="Path where interaction will be saved. If None or empty string interaction won't be saved",
    )
    parser.add_argument(
        "--pdb", default=False, action="store_true", help="Run with pdb"
    )


def get_params(
    n_senders: bool,
    n_recvs: bool,
):
    params = dict(
        n_senders=n_senders,
        n_recvs=n_recvs,
    )

    distributed_context = argparse.Namespace(is_distributed=False)
    params_fixed = dict(
        vocab_size=2048,
        #
        pretrain_vision=True,
        vision_model_name="resnet50",
        #
        gs_temperature=5.0,
        gs_temperature_decay=1.0,
        update_gs_temp_frequency=1,
        train_gs_temperature=False,
        straight_through=False,
        #
        recv_temperature=0.1,
        recv_hidden_dim=2048,
        recv_output_dim=2048,
        #
        random_seed=111,
        distributed_context=distributed_context,
    )
    params.update(params_fixed)

    params = argparse.Namespace(**params)

    random.seed(params.random_seed)
    torch.manual_seed(params.random_seed)
    np.random.seed(params.random_seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(params.random_seed)
    return params


def get_game(params: argparse.Namespace, checkpoint_path: str):
    game = build_game(params)
    checkpoint = torch.load(checkpoint_path)
    game.load_state_dict(checkpoint.model_state_dict)
    return game


def save_interaction(interaction: Interaction, log_dir: Union[pathlib.Path, str]):
    dump_dir = pathlib.Path(log_dir)
    dump_dir.mkdir(exist_ok=True, parents=True)
    torch.save(interaction, dump_dir / "interactions_test_set.pt")


def get_test_data(
    dataset_dir: str = "/datasets01/imagenet_full_size/061417/train",
    dataset_name: str = "imagenet",
    batch_size: int = 128,
    image_size: int = 224,
    num_workers: int = 4,
):

    transformations = ImageTransformation(
        size=image_size,
        augmentation=False,
        return_original_image=False,
        dataset_name=dataset_name,
    )
    if dataset_name == "cifar10":
        dataset = datasets.CIFAR10(
            root="./data", train=True, download=True, transform=transformations
        )
    else:
        dataset = datasets.ImageFolder(dataset_dir, transform=transformations)

    def collate_fn(batch):
        return (
            torch.stack([x[0][0] for x in batch], dim=0),
            torch.cat([torch.Tensor([x[1]]).long() for x in batch], dim=0),
            torch.stack([x[0][1] for x in batch], dim=0),
        )

    return torch.utils.data.DataLoader(
        dataset,
        batch_size=batch_size,
        num_workers=num_workers,
        collate_fn=collate_fn,
        pin_memory=True,
        drop_last=True,
    )


def add_reshaped_interaction_fields(
    interaction: Interaction, n_senders: int, n_recvs: int, batch_size: int
):
    interaction.aux["reshaped_receiver_output"] = interaction.receiver_output.view(
        -1, n_senders * n_recvs, batch_size, batch_size
    ).detach()

    vocab_size = interaction.message.shape[-1]
    interaction.aux["reshaped_message"] = torch.argmax(
        interaction.message.view(-1, batch_size, n_senders * n_recvs, vocab_size), dim=1
    ).detach()

    """
    acc = (
        (
            torch.argmax(interaction.aux["reshaped_receiver_output"], dim=-1).view(-1)
            == torch.arange(32).repeat(312 * 9)
        )
        .float()
        .mean()
    )
    print(f"acc {acc}")
    """


def evaluate(game, data, device, n_senders, n_recvs):
    mean_loss = 0.0
    interactions = []
    n_batches = 0
    if torch.cuda.is_available():
        game.cuda()
    game.eval()
    with torch.no_grad():
        for batch in data:
            if not isinstance(batch, Batch):
                batch = Batch(*batch)
            batch = batch.to(device)
            for _ in range(n_senders * n_recvs):
                optimized_loss, interaction = game(*batch)

                interaction = interaction.to("cpu")
                mean_loss += optimized_loss

                interactions.append(interaction)
            n_batches += 1

    mean_loss /= n_batches * n_senders * n_recvs
    full_interaction = Interaction.from_iterable(interactions)
    return mean_loss.item(), full_interaction
