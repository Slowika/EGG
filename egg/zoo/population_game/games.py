# Copyright (c) Facebook, Inc. and its affiliates.

# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import torch

from egg.core.interaction import LoggingStrategy
from egg.zoo.population_game.archs import (
    EmComSSLSymbolGame,
    EmSSLSender,
    Receiver,
    VisionGameWrapper,
    VisionModule,
    get_vision_modules,
)
from egg.zoo.population_game.losses import XEntLoss
from egg.core.population import UniformAgentSampler, PopulationGame


def build_vision_encoder(
    model_name: str = "resnet50",
    shared_vision: bool = False,
    pretrain_vision: bool = False,
):
    (
        sender_vision_module,
        receiver_vision_module,
        visual_features_dim,
    ) = get_vision_modules(
        encoder_arch=model_name, shared=shared_vision, pretrain_vision=pretrain_vision
    )
    vision_encoder = VisionModule(
        sender_vision_module=sender_vision_module,
        receiver_vision_module=receiver_vision_module,
    )
    return vision_encoder, visual_features_dim


def build_game(opts):
    vision_encoder, visual_features_dim = build_vision_encoder(
        model_name=opts.model_name,
        shared_vision=opts.shared_vision,
        pretrain_vision=opts.pretrain_vision,
    )

    train_logging_strategy = LoggingStrategy(
        False, False, True, False, True, True, False
    )
    test_logging_strategy = LoggingStrategy(
        False, False, True, False, True, True, False
    )

    senders = [
        EmSSLSender(
            input_dim=visual_features_dim,
            hidden_dim=opts.projection_hidden_dim,
            output_dim=opts.projection_output_dim,
            temperature=opts.gs_temperature,
            trainable_temperature=opts.train_gs_temperature,
            straight_through=opts.straight_through,
        )
        for _ in range(opts.n_senders)
    ]

    receivers = [
        Receiver(
            input_dim=visual_features_dim,
            hidden_dim=opts.projection_hidden_dim,
            output_dim=opts.projection_output_dim,
        )
        for _ in range(opts.n_recvs)
    ]

    loss = [
        XEntLoss(
            temperature=opts.loss_temperature,
            similarity=opts.similarity,
        )
    ]

    agents_loss_sampler = UniformAgentSampler(
        senders,
        receivers,
        loss,
    )

    game = EmComSSLSymbolGame(
        train_logging_strategy=train_logging_strategy,
        test_logging_strategy=test_logging_strategy,
    )

    game = VisionGameWrapper(game, vision_encoder)

    game = PopulationGame(game, agents_loss_sampler)

    if opts.distributed_context.is_distributed:
        game = torch.nn.SyncBatchNorm.convert_sync_batchnorm(game)

    return game
