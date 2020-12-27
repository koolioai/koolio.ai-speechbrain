#!/usr/bin/env/python3
"""This minimal example trains a RNNT-based speech recognizer on a tiny dataset.
The encoder is based on a combination of convolutional, recurrent, and
feed-forward networks (CRDNN) that predict phonemes.  A beamsearch is used on
top of the output probabilities.
Given the tiny dataset, the expected behavior is to overfit the training dataset
(with a validation performance that stays high).
"""
import pytest
import pathlib
import speechbrain as sb


class TransducerBrain(sb.Brain):
    def compute_forward(self, batch, stage):
        "Given an input batch it computes the output probabilities."
        batch = batch.to(self.device)
        wavs, lens = batch.sig
        feats = self.modules.compute_features(wavs)
        feats = self.modules.mean_var_norm(feats, lens)

        # Transcription network: input-output dependency
        TN_output = self.modules.enc(feats)
        TN_output = self.modules.enc_lin(TN_output)

        # Prediction network: output-output dependency
        targets, target_lens = batch.phn_encoded_bos
        PN_output = self.modules.emb(targets)
        PN_output, _ = self.modules.dec(PN_output)
        PN_output = self.modules.dec_lin(PN_output)

        # Joint the networks
        joint = self.modules.Tjoint(
            TN_output.unsqueeze(2), PN_output.unsqueeze(1),
        )
        outputs = self.modules.output(joint)
        outputs = self.hparams.log_softmax(outputs)
        if stage == sb.Stage.TRAIN:
            return outputs, lens
        else:
            hyps, scores, _, _ = self.hparams.searcher(TN_output)
            return outputs, lens, hyps

    def compute_objectives(self, predictions, batch, stage):
        "Given the network predictions and targets computed the CTC loss."
        phns, phn_lens = batch.phn_encoded

        if stage == sb.Stage.TRAIN:
            predictions, lens = predictions
        else:
            predictions, lens, seq = predictions
            self.per_metrics.append(batch.id, seq, phns, target_len=phn_lens)

        loss = self.hparams.compute_cost(
            predictions,
            phns.to(self.device).long(),
            lens,
            phn_lens.to(self.device),
        )
        return loss

    def on_stage_start(self, stage, epoch=None):
        "Gets called when a stage (either training, validation, test) starts."
        if stage != sb.Stage.TRAIN:
            self.per_metrics = self.hparams.per_stats()

    def on_stage_end(self, stage, stage_loss, epoch=None):
        """Gets called at the end of a stage."""
        if stage == sb.Stage.TRAIN:
            self.train_loss = stage_loss
        if stage == sb.Stage.VALID and epoch is not None:
            print("Epoch %d complete" % epoch)
            print("Train loss: %.2f" % self.train_loss)
        if stage != sb.Stage.TRAIN:
            print(stage, "loss: %.2f" % stage_loss)
            print(stage, "PER: %.2f" % self.per_metrics.summarize("error_rate"))


def data_prep(data_folder, hparams):
    "Creates the datasets and their data processing pipelines."

    # 1. Declarations:
    train_data = sb.data_io.dataset.DynamicItemDataset.from_json(
        json_path=data_folder / "train.json",
        replacements={"data_root": data_folder},
    )
    valid_data = sb.data_io.dataset.DynamicItemDataset.from_json(
        json_path=data_folder / "dev.json",
        replacements={"data_root": data_folder},
    )
    datasets = [train_data, valid_data]
    label_encoder = sb.data_io.encoder.CTCTextEncoder()

    # 2. Define audio pipeline:
    @sb.utils.data_pipeline.takes("wav")
    @sb.utils.data_pipeline.provides("sig")
    def audio_pipeline(wav):
        sig = sb.data_io.data_io.read_audio(wav)
        return sig

    sb.data_io.dataset.add_dynamic_item(datasets, audio_pipeline)

    # 3. Define text pipeline:
    @sb.utils.data_pipeline.takes("phn")
    @sb.utils.data_pipeline.provides(
        "phn_list", "phn_encoded", "phn_encoded_bos"
    )
    def text_pipeline(phn):
        phn_list = phn.strip().split()
        yield phn_list
        phn_encoded = label_encoder.encode_sequence_torch(phn_list)
        yield phn_encoded
        phn_encoded_bos = label_encoder.prepend_bos_index(phn_encoded).long()
        yield phn_encoded_bos

    sb.data_io.dataset.add_dynamic_item(datasets, text_pipeline)

    # 3. Fit encoder:
    # NOTE: In this minimal example, also update from valid data
    label_encoder.insert_blank(index=hparams["blank_index"])
    label_encoder.insert_bos_eos(
        bos_index=hparams["bos_index"], eos_label="<bos>"
    )
    label_encoder.update_from_didataset(train_data, output_key="phn_list")
    label_encoder.update_from_didataset(valid_data, output_key="phn_list")

    # 4. Set output:
    sb.data_io.dataset.set_output_keys(
        datasets, ["id", "sig", "phn_encoded", "phn_encoded_bos"]
    )
    return train_data, valid_data, label_encoder


def main():
    pytest.importorskip("numba")
    experiment_dir = pathlib.Path(__file__).resolve().parent
    hparams_file = experiment_dir / "hyperparams.yaml"
    data_folder = "../../../../samples/audio_samples/nn_training_samples"
    data_folder = (experiment_dir / data_folder).resolve()

    # Load model hyper parameters:
    with open(hparams_file) as fin:
        hparams = sb.load_extended_yaml(fin)

    # Dataset creation
    train_data, valid_data, label_encoder = data_prep(data_folder, hparams)

    # Trainer initialization
    trasducer_brain = TransducerBrain(
        hparams["modules"], hparams["opt_class"], hparams
    )

    # Training/validation loop
    trasducer_brain.fit(
        range(hparams["N_epochs"]),
        train_data,
        valid_data,
        **hparams["dataloader_options"],
    )
    # Evaluation is run separately (now just evaluating on valid data)
    trasducer_brain.evaluate(valid_data)

    # Check that model overfits for integration test
    assert trasducer_brain.train_loss < 1.0


if __name__ == "__main__":
    main()


def test_error():
    main()
