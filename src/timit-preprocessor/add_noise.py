from functools import partial
from multiprocessing.dummy import Pool
from scipy.io import wavfile
import argparse
from glob import glob
import os, sys
import numpy as np


def gen_noise(n, type, sigma, noise_folder="../../data/NoiseDB/NoiseX_16kHz"):
    """Generate noise of a certain type and std."""

    if type == "white":
        noise_filename = os.path.join(noise_folder, "{}_16kHz.wav".format(type))
        _, loaded_noise = wavfile.read(noise_filename)
        try:
            assert(n < loaded_noise.shape[0])

        except AssertionError as e:
            print("Noise file: {} is too short.".format(noise_filename), file=sys.stderr)

        # Find a random section in file.
        istart = np.random.randint(loaded_noise.shape[0] - n)
        raw_noise = loaded_noise[istart:istart+n]

    else:
        print("Unknown {} noise".format(type), file=sys.stderr)
        raw_noise = 0

    return raw_noise * sigma


def new_filename(file, type, snr):
    """Append noise tyep and power at the end of wav filename."""
    return file.replace(".WAV", ".WAV.{}.{}dB".format(type, snr))


def corrupt_data(s, type, snr):
    """Corrupt a signal with a particular noise."""
    s_std = np.std(s)
    n_std = 10 ** (- snr / 10) * s_std
    n = gen_noise(s.shape[0], type, n_std)
    sn = (s + n).astype(s.dtype)
    return sn


def corrupt_wav(file, type=None, snr=None):
    """Corrupt a wav file with noise and write to a new file."""
    rate, s = wavfile.read(file)
    sn = corrupt_data(s, type, snr)
    wavfile.write(new_filename(file, type, snr), rate, sn)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add a particular noise type to WAV files.")

    parser.add_argument('-i', metavar="<Input folder>", type=str)
    parser.add_argument('-snr', metavar="<Signal to Noise Ratio (dB)>", type=int)
    parser.add_argument('-type', metavar="<Noise type>", type=str)
    parser.add_argument('-j', metavar="<Number of jobs (default: numcpu)>",
                        type=int, default=os.cpu_count())

    args = parser.parse_args()

    print(args.i, args.snr, args.type)

    wavs = glob(os.path.join(args.i, "**" , "*.WAV"), recursive=True)
    f = partial(corrupt_wav, type=args.type, snr=args.snr)
    f(wavs[1])
    with Pool(args.j) as pool:
        pool.map(f, wavs)

    sys.exit(0)