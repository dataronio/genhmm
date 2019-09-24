import sys
import pickle as pkl
import os
import glob
from parse import parse
from gm_hmm.src.genHMM import GenHMMclassifier
from gm_hmm.src.ref_hmm import GaussianHMMclassifier
from gm_hmm.src.utils import save_model

if __name__ == "__main__":
    usage = "Aggregate models from several classes.\n" \
            "Usage: python bin/aggregate_models.py models/epoch1.mdl"

    if len(sys.argv) != 2 or sys.argv[1] == "-h" or sys.argv[1] == "--help":
        print(usage)
        sys.exit(1)

    out_mdl_file = sys.argv[1]

    # Find the class digit
    get_sort_key = lambda x: parse("{}class{:d}.mdlc", x)[1]

    # Find the model used, 'gen' or 'gaus'
    model_type_ = parse("{}/models/{}/{}.mdl", out_mdl_file)[1]

    if "gaus" in model_type_:
        model_type = "gaus"
    elif "gen" in model_type_:
        model_type = "gen"
    else:
        print("No known model type found in {}".format(out_mdl_file),file=sys.stderr)
        sys.exit(1)

    # Find all trained classes submodels
    in_mdlc_files = sorted(glob.glob(out_mdl_file.replace(".mdl", "_class*.mdlc")), key=get_sort_key)
    if model_type == 'gaus':
        mdl = GaussianHMMclassifier(mdlc_files=in_mdlc_files)
        assert(all([int(h.iclass) == int(i)+1 for i, h in enumerate(mdl.hmms)]))
    
    elif model_type == 'gen':
        mdl = GenHMMclassifier(mdlc_files=in_mdlc_files)
        assert(all([int(h.iclass) == int(i)+1 for i, h in enumerate(mdl.hmms)]))

    else:
        print("(should have been caught earlier) Unknown model type: {}".format(model_type), file=sys.stderr)
        sys.exit(1)

    save_model(mdl, out_mdl_file)
    sys.exit(0)
