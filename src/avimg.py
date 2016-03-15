# coding=utf-8
# avimg.py: unimodal average image based classifier.

from __future__ import print_function
import argparse
import os
import random
import glob
import cPickle
from datetime import datetime

import numpy as np
from keras.optimizers import Adam
from keras.callbacks import ModelCheckpoint, Callback
from keras.preprocessing.image import ImageDataGenerator
from scipy.misc import imread

from models.vgg16 import VGG16


SPLIT_DIR = "data/perssplit"
PICKLED_LABEL_FILE = "data/labels.pickle"
PERS_FIELD_NAME = "Answer.q7_persuasive"
DEFAULT_LEARNING_RATES = [0.0001]
DEFAULT_EPOCHS = 1
DEFAULT_BATCH_SIZE = 100


with open(PICKLED_LABEL_FILE, "rb") as lf:
    labels_map = cPickle.load(lf)


def generate_batch(batch_ims):
    """Generate a batch (X, y) from a list of images."""
    batch_X = np.zeros((len(batch_ims), 3, 224, 224))
    batch_y = np.zeros((len(batch_ims), 1))
    for i, im_file in enumerate(batch_ims):
        img = imread(im_file).astype(np.float32)
        img[:, :, 0] -= 103.939
        img[:, :, 1] -= 116.779
        img[:, :, 2] -= 123.68
        img = img.transpose((2, 0, 1)).astype(np.float32)
        batch_X[i, :, :, :] = img

        file_id = im_file.split("/")[-1].split(".")[0]
        score = labels_map[file_id][PERS_FIELD_NAME]
        if score >= 5.5:
            batch_y[i] = 1
    return (batch_X, batch_y)


class RandomBatchGenerator(object):

    """Generate random batches of data."""

    def __init__(self, batch_size, typs, imdir, augment, randomize):
        # typs should be a list of "train", "val", or "test".
        self._batch_size = batch_size
        self._randomize = randomize
        self._idx = 0
        if augment is True:
            self._datagen = ImageDataGenerator(
                featurewise_center=False,
                samplewise_center=False,
                featurewise_std_normalization=False,
                samplewise_std_normalization=False,
                zca_whitening=False,
                rotation_range=0,
                width_shift_range=0,
                height_shift_range=0,
                shear_range=0,
                horizontal_flip=True,
                vertical_flip=True
            )
        else:
            self._datagen = None
        for typ in set(typs):
            vids_file = os.path.join(SPLIT_DIR, "{}.txt".format(typ))
            with open(vids_file) as vf:
                self._ims = [os.path.join(imdir, line.strip() + ".jpg") for line in vf]

    def __iter__(self):
        return self

    def next(self):
        if self._randomize:
            batch_ims = random.sample(self._ims, self._batch_size)
        else:
            batch_ims = self._ims[self._idx:self._idx+self._batch_size]
            self._idx += self._batch_size
            if self._idx >= len(self._ims):
                self._idx = 0
        batch_X, batch_y = generate_batch(batch_ims)
        if self._datagen is None:
            return batch_X, batch_y
        else:
            return next(self._datagen.flow(
                X=batch_X,
                y=batch_y,
                batch_size=self._batch_size,
                shuffle=False
            ))


class BatchLossHistory(Callback):
    def on_train_begin(self, logs={}):
        self.losses = []
        self.accs = []

    def on_batch_end(self, batch, logs={}):
        self.losses.append(logs.get("loss"))
        self.accs.append(logs.get("acc"))


if __name__=="__main__":
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("--imdir", type=str, required=True)
    arg_parser.add_argument("--vgg-weights", type=str, required=True)
    arg_parser.add_argument("--save-path", type=str, required=True)
    arg_parser.add_argument("--lrs", type=float, nargs="+", default=DEFAULT_LEARNING_RATES)
    arg_parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS)
    arg_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    arg_parser.add_argument("--train", type=str, choices=["true", "false"], required=True)
    arg_parser.add_argument("--default-arch-weights", type=str, choices=["true", "false"], required=True)
    arg_parser.add_argument("--augment", type=str, choices=["true", "false"], required=True)
    args = arg_parser.parse_args()

    default_arch_weights = args.default_arch_weights == "true"
    date = str(datetime.now().date())
    base_save_dir = os.path.join(args.save_path, date)
    os.makedirs(base_save_dir)

    if args.train == "true":
        final_train_accs = {}
        final_val_accs = {}
        for lr in args.lrs:
            print("LR: {}".format(lr))
            print("Building model")
            model = VGG16(args.vgg_weights, default_arch_weights)
            model.compile(optimizer=Adam(lr=lr), loss="binary_crossentropy", class_mode="binary")
            print("Model built")

            save_path = os.path.join(base_save_dir, "lr{}".format(lr))
            os.makedirs(save_path)

            train_generator = RandomBatchGenerator(args.batch_size, ["train"], args.imdir, args.augment=="true", True)
            val_generator = RandomBatchGenerator(args.batch_size, ["val"], args.imdir, args.augment=="true", True)

            ckpt_clbk = ModelCheckpoint(filepath=os.path.join(save_path, "checkpoint.h5"), verbose=0, save_best_only=False)
            batch_hist_clbk = BatchLossHistory()

            history = model.fit_generator(
                generator=train_generator,
                samples_per_epoch=len(train_generator._ims),
                nb_epoch=args.epochs,
                verbose=1,
                show_accuracy=True,
                callbacks=[ckpt_clbk, batch_hist_clbk],
                validation_data=val_generator,
                nb_val_samples=len(val_generator._ims) // 4,
                nb_worker=1
            )

            fixed_train_generator = RandomBatchGenerator(args.batch_size, ["train"], args.imdir, False, False)
            fixed_val_generator = RandomBatchGenerator(args.batch_size, ["val"], args.imdir, False, False)

            _, final_train_accs[lr] = model.evaluate_generator(
                generator=fixed_train_generator,
                val_samples=len(fixed_train_generator._ims),
                show_accuracy=True,
                verbose=2
            )
            _, final_val_accs[lr] = model.evaluate_generator(
                generator=fixed_val_generator,
                val_samples=len(fixed_val_generator._ims),
                show_accuracy=True,
                verbose=2
            )
            print("LR {} final train acc: {}; final val acc: {}".format(lr, final_train_accs[lr], final_val_accs[lr]))

            model.save_weights(os.path.join(save_path, "weights.h5"), overwrite=True)
            print(history.history["acc"], file=open(os.path.join(save_path, "epoch_train_accs.txt"), "w"))
            print(history.history["loss"], file=open(os.path.join(save_path, "epoch_train_losses.txt"), "w"))
            print(history.history["val_acc"], file=open(os.path.join(save_path, "epoch_val_accs.txt"), "w"))
            print(history.history["val_loss"], file=open(os.path.join(save_path, "epoch_val_losses.txt"), "w"))
            print(batch_hist_clbk.accs, file=open(os.path.join(save_path, "batch_accs.txt"), "w"))
            print(batch_hist_clbk.losses, file=open(os.path.join(save_path, "batch_losses.txt"), "w"))

        print(final_train_accs, file=open(os.path.join(base_save_dir, "final_train_accs.txt"), "w"))
        print(final_val_accs, file=open(os.path.join(base_save_dir, "final_val_accs.txt"), "w"))
        
        best_lr = max(final_val_accs, key=lambda x: final_val_accs[x])
        print("Best learning rate: {}".format(best_lr))
    else:
        best_lr = DEFAULT_LEARNING_RATES[0]
    
    print("Building model")
    model = VGG16(args.vgg_weights, default_arch_weights)
    model.compile(optimizer=Adam(lr=best_lr), loss="binary_crossentropy", class_mode="binary")
    print("Model built")

    if args.train == "true":
        print("Training best model on training and validation set")
        save_path = os.path.join(base_save_dir, "best_lr")
        os.makedirs(save_path)

        ckpt_clbk = ModelCheckpoint(filepath=os.path.join(save_path, "checkpoint.h5"), verbose=0, save_best_only=False)
        batch_hist_clbk = BatchLossHistory()

        train_val_generator = RandomBatchGenerator(args.batch_size, ["train", "val"],args.imdir, args.augment=="true", True)

        history = model.fit_generator(
            generator=train_val_generator,
            samples_per_epoch=len(train_val_generator._ims),
            nb_epoch=args.epochs,
            verbose=1,
            show_accuracy=True,
            callbacks=[ckpt_clbk, batch_hist_clbk],
            nb_worker=1
        )

        print(history.history["acc"], file=open(os.path.join(save_path, "epoch_train_accs.txt"), "w"))
        print(history.history["loss"], file=open(os.path.join(save_path, "epoch_train_losses.txt"), "w"))
        print(batch_hist_clbk.accs, file=open(os.path.join(save_path, "batch_accs.txt"), "w"))
        print(batch_hist_clbk.losses, file=open(os.path.join(save_path, "batch_losses.txt"), "w"))

    test_generator = RandomBatchGenerator(args.batch_size, ["test"], args.imdir, False, False)

    _, test_acc = model.evaluate_generator(
        generator=test_generator,
        val_samples=len(test_generator._ims),
        show_accuracy=True,
        verbose=2
    )
    print("Test acc: {}".format(test_acc))

    if args.train == "true":
        summary = {
            "best_lr": best_lr,
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "test_accuracy": test_acc,
        }
        print(summary, file=open(os.path.join(base_save_dir, "summary.txt"), "w"))

