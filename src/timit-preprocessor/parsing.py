#!/usr/bin/env python

import os
import sys
import argparse
from parse import parse


def main(args=None):
    try:
        target, ntype, snr = parse("{}.{}.{:d}dB", args.folder)
    except TypeError as e:
        target = args.folder
        ntype = "clean"
        snr = 0

    target_folder = os.path.join(args.datapath, target.upper())

    #target = args.folder

    features = '{}/{}.wav.scp'.format(MATERIAL_PATH, args.folder)
    labels = '{}/{}.lbl'.format(MATERIAL_PATH, args.folder)
    if ntype == "clean":
        extension = ".WAV"
    else:
        extension = ".WAV.{}.{}dB".format(ntype, snr)
    
    with open(features, "w+") as f, open(labels, "w+") as l:
        for person in os.listdir(target_folder):
            person_dir = os.path.join(target_folder, person)
            for task in os.listdir(person_dir):
                task_dir = os.path.join(person_dir, task)
                raw_sentences = [x.split('.')[0] for x in os.listdir(task_dir)]
                sentences = list(set(raw_sentences))
                for sentence in sentences:
                    wav_file = os.path.join(task_dir, sentence + extension)
                    f.write('{}-{}-{} {}\n'.format(person, task, sentence, os.path.abspath(wav_file)))

                    phone_seq_file = os.path.join(task_dir, sentence+'.PHN')
                    phone_seq = []
                    with open(phone_seq_file, "r+") as pf:
                        for line in pf:
                            phone_seq.append(line.split(' ')[-1].strip())
                    l.write('{}-{}-{} {}\n'.format(person, task, sentence, ','.join(phone_seq)))
            

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="parsing .wav.scp files for advanced use\n"
                    "e.g. python3 parsing.py ~/Workspace/data/timit train", formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('datapath',
                        metavar="<timit relative path>", type=str)
    parser.add_argument('folder',
                        metavar="<train|test>", type=str)

    args = parser.parse_args()

    MATERIAL_PATH = '.data/material'
    if not os.path.exists(MATERIAL_PATH):
        os.makedirs(MATERIAL_PATH)

    main(args)
