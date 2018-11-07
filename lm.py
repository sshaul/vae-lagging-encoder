import sys
import time
import argparse

import numpy as np

import torch
from torch import nn, optim

from data import MonoTextData

from modules import LSTM_LM

max_decay = 5

def init_config():
    parser = argparse.ArgumentParser(description='VAE mode collapse study')

    # model hyperparameters
    parser.add_argument('--ni', type=int, default=512, help='word embedding size')
    parser.add_argument('--nh', type=int, default=1024, help='LSTM hidden state size')
    parser.add_argument('--dropout_in', type=float, default=0.5, help='LSTM decoder dropout')
    parser.add_argument('--dropout_out', type=float, default=0.5, help='LSTM decoder dropout')

    # optimization parameters
    parser.add_argument('--lr', type=float, default=1.0, help='Learning rate')
    parser.add_argument('--lr_decay', type=float, default=0.5, help='Learning rate')
    parser.add_argument('--decay_epoch', type=int, default=5)
    parser.add_argument('--clip_grad', type=float, default=5.0, help='')
    parser.add_argument('--optim', type=str, default='adam', help='')
    parser.add_argument('--epochs', type=int, default=40,
                        help='number of training epochs')
    parser.add_argument('--batch_size', type=int, default=32, help='batch size')


    # data parameters
    parser.add_argument('--label', type=int, default=0, help='batch size')
    parser.add_argument('--train_data', type=str, default='datasets/yahoo/data_yahoo_release/train.txt',
                        help='training data file')
    parser.add_argument('--val_data', type=str, default='datasets/yahoo/data_yahoo_release/train.txt',
                        help='training data file')
    parser.add_argument('--test_data', type=str, default='datasets/yahoo/data_yahoo_release/test.txt',
                        help='testing data file')

    # log parameters
    parser.add_argument('--niter', type=int, default=50, help='report every niter iterations')
    parser.add_argument('--nepoch', type=int, default=1, help='valid every nepoch epochs')

    # others
    parser.add_argument('--seed', type=int, default=783435, metavar='S', help='random seed')
    parser.add_argument('--save_path', type=str, default='', help='valid every nepoch epochs')
    parser.add_argument('--cuda', action='store_true', default=False, help='use gpu')


    args = parser.parse_args()
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if args.cuda:
        torch.cuda.manual_seed(args.seed)

    return args

def test(model, test_data_batch, args):

    report_loss = 0
    report_num_words = report_num_sents = 0
    for i in np.random.permutation(len(test_data_batch)):
        batch_data = test_data_batch[i]
        batch_size, sent_len = batch_data.size()

        # not predict start symbol
        report_num_words += (sent_len - 1) * batch_size

        report_num_sents += batch_size


        loss = model.reconstruct_error(batch_data)


        loss = loss.sum()

        report_loss += loss.item()

    nll = (report_loss) / report_num_sents
    ppl = np.exp(nll * report_num_sents / report_num_words)

    print('avg_loss: %.4f, nll: %.4f, ppl: %.4f' % \
           (nll, nll, ppl))
    sys.stdout.flush()

    return nll, ppl

def main(args):

    class uniform_initializer(object):
        def __init__(self, stdv):
            self.stdv = stdv
        def __call__(self, tensor):
            nn.init.uniform_(tensor, -self.stdv, self.stdv)


    class xavier_normal_initializer(object):
        def __call__(self, tensor):
            nn.init.xavier_normal_(tensor)

    if args.cuda:
        print('using cuda')

    print('model saving path: %s' % args.save_path)

    print(args)

    opt_dict = {"not_improved": 0, "lr": args.lr, "best_loss": 1e4}

    train_data = MonoTextData(args.train_data, label=args.label)

    vocab = train_data.vocab
    vocab_size = len(vocab)

    val_data = MonoTextData(args.val_data, label=args.label, vocab=vocab)
    test_data = MonoTextData(args.test_data, label=args.label, vocab=vocab)

    print('Train data: %d samples' % len(train_data))
    print('finish reading datasets, vocab size is %d' % len(vocab))
    print('dropped sentences: %d' % train_data.dropped)
    sys.stdout.flush()

    model_init = uniform_initializer(0.01)
    emb_init = uniform_initializer(0.1)

    device = torch.device("cuda" if args.cuda else "cpu")
    args.device = device
    lm = LSTM_LM(args, vocab, model_init, emb_init).to(device)

    if args.optim == 'sgd':
        optimizer = optim.SGD(lm.parameters(), lr=opt_dict["lr"])
    else:
        optimizer = optim.Adam(lm.parameters(), lr=opt_dict["lr"], betas=(0.5, 0.999))

    iter_ = decay_cnt =  0
    best_loss = 1e4
    best_nll = best_ppl = 0
    lm.train()
    start = time.time()

    train_data_batch = train_data.create_data_batch(batch_size=args.batch_size,
                                                    device=device,
                                                    batch_first=True)
    val_data_batch = val_data.create_data_batch(batch_size=args.batch_size,
                                                device=device,
                                                batch_first=True)
    test_data_batch = test_data.create_data_batch(batch_size=args.batch_size,
                                                  device=device,
                                                  batch_first=True)
    for epoch in range(args.epochs):
        report_loss = 0
        report_num_words = report_num_sents = 0
        for i in np.random.permutation(len(train_data_batch)):
            batch_data = train_data_batch[i]
            batch_size, sent_len = batch_data.size()

            # not predict start symbol
            report_num_words += (sent_len - 1) * batch_size

            report_num_sents += batch_size

            optimizer.zero_grad()

            loss = lm.reconstruct_error(batch_data)

            report_loss += loss.sum().item()
            loss = loss.mean(dim=-1)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(lm.parameters(), args.clip_grad)

            optimizer.step()

            if iter_ % args.niter == 0:
                train_loss = report_loss / report_num_sents

                print('epoch: %d, iter: %d, avg_loss: %.4f, time elapsed %.2fs' %
                       (epoch, iter_, train_loss, time.time() - start))
                sys.stdout.flush()

            iter_ += 1

            # if iter_ >= args.stop_niter and args.stop_niter > 0:
            #     return

        if epoch % args.nepoch == 0:
            print('epoch: %d, testing' % epoch)
            lm.eval()

            with torch.no_grad():
                nll, ppl = test(lm, test_data_batch, args)
                nll, ppl = test(lm, val_data_batch, args)

            if nll < best_loss:
                print('update best loss')
                best_loss = nll
                best_nll = nll
                best_ppl = ppl
                torch.save(lm.state_dict(), args.save_path)

            if nll > opt_dict["best_loss"]:
                opt_dict["not_improved"] += 1
                if opt_dict["not_improved"] >= args.decay_epoch and epoch >= 15:
                    opt_dict["best_loss"] = loss
                    opt_dict["not_improved"] = 0
                    opt_dict["lr"] = opt_dict["lr"] * args.lr_decay
                    print('new lr: %f' % opt_dict["lr"])
                    decay_cnt += 1
                    if args.optim == 'sgd':
                        optimizer = optim.SGD(lm.parameters(), lr=opt_dict["lr"])
                    else:
                        optimizer = optim.Adam(lm.parameters(), lr=opt_dict["lr"], betas=(0.5, 0.999))
            else:
                opt_dict["not_improved"] = 0
                opt_dict["best_loss"] = nll

            if decay_cnt == max_decay:
                break

            lm.train()

    print('best_loss: %.4f, nll: %.4f, ppl: %.4f' \
          % (best_loss, best_nll, best_ppl))
    sys.stdout.flush()

    lm.load_state_dict(torch.load(args.save_path))
    lm.eval()
    with torch.no_grad():
        nll, ppl = test(lm, test_data_batch, args)
    # vae.eval()
    # calc_nll(vae, test_data, args)

if __name__ == '__main__':
    args = init_config()
    main(args)
