import warnings

warnings.filterwarnings('ignore')

import tensorflow as tf
import numpy as np
import pickle
import getopt
import time
import sys
import os
import json

from recurrent_controller import RecurrentController
sys.path.append('./dnc')

from dnc.dnc import DNC


def llprint(message):
    """
    Flushes message to stdout
    :param message: A string to print.
    :return: None.
    """
    sys.stdout.write(message)
    sys.stdout.flush()


def load(path):
    """
    Unpickle the file located at path.
    :param path: The path to the pickled file.
    :return: Returns the object hierarchy stored in the file.
    """
    return pickle.load(open(path, 'rb'))


def onehot(index, size):
    """
    Create a numpy vector that has all zeros except at index. index has the value 1.
    :param index: The index where the vector should be one.
    :param size: The length of the vector.
    :return: A one-hot vector encoding for the given index.
    """
    vec = np.zeros(size, dtype=np.float32)
    vec[index] = 1.0
    return vec


def prepare_sample(sample, target_code, word_space_size):
    """
    Transform a sample into input and output vectors.
    :param sample: the dialogue connected by '+' characters followed by the '\' character and then a question. Where
                    the question is followed by the target_code and the answer (with all words encoded).
    :param target_code: code indicating end of sample and beginning of answer (also used in input as
                        a replacement for each letter in the answer.
    :param word_space_size: how many total letters exist.
    :return: tuple including input vector, output vector, length of sequence, and associated weights.
    """
    input_vec = np.array(sample[:sample.index(target_code)], dtype=np.float32)
    output_vec = sample[sample.index(target_code) + 1:]
    while len(output_vec) < len(input_vec):
        output_vec.append(target_code)
    output_vec = np.array(output_vec, dtype=np.float32)
    seq_len = input_vec.shape[0]
    weights_vec = np.zeros(seq_len, dtype=np.float32)

    target_mask = (output_vec != target_code)
    weights_vec[target_mask] = 1.0
    input_vec = np.array([onehot(int(code), word_space_size) for code in input_vec])
    output_vec = np.array([onehot(int(code), word_space_size) for code in output_vec])

    return (
        np.reshape(input_vec, (1, -1, word_space_size)),
        np.reshape(output_vec, (1, -1, word_space_size)),
        seq_len,
        np.reshape(weights_vec, (1, -1, 1))
    )


def main():
    """
    Train the DNC to take answer questions from the DREAM dataset.
    :return: None.
    """
    os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
    tf.compat.v1.logging.set_verbosity(tf.compat.v1.logging.ERROR)
    dirname = os.path.dirname(__file__)
    ckpts_dir = os.path.join(dirname, 'checkpoints/')
    data_dir = os.path.join(dirname, 'data', 'encoded')
    tb_logs_dir = os.path.join(dirname, 'logs')

    llprint("Loading Data ... ")
    lexicon_dict = load(os.path.join(data_dir, 'lexicon-dict.pkl'))
    data_files = os.listdir(os.path.join(data_dir, 'train'))
    llprint("Done!\n")

    batch_size = 1
    input_size = output_size = len(lexicon_dict)
    sequence_max_length = 100
    word_space_size = len(lexicon_dict)
    words_count = 256
    word_size = 64
    read_heads = 4

    learning_rate = 1e-4
    momentum = 0.9

    from_checkpoint = None
    iterations = 100000

    start_step = 0

    options, _ = getopt.getopt(sys.argv[1:], '', ['checkpoint=', 'iterations=', 'start='])

    for opt in options:
        if opt[0] == '--checkpoint':
            from_checkpoint = opt[1]
            print("Checkpoint found")
        elif opt[0] == '--iterations':
            iterations = int(opt[1])
        elif opt[0] == '--start':
            start_step = int(opt[1])

    graph = tf.Graph()
    with graph.as_default():
        with tf.compat.v1.Session(graph=graph) as session:

            llprint("Building Computational Graph ... ")

            optimizer = tf.compat.v1.train.RMSPropOptimizer(learning_rate, momentum=momentum)
            summarizer = tf.compat.v1.summary.FileWriter(tb_logs_dir, session.graph)

            ncomputer = DNC(
                RecurrentController,
                input_size,
                output_size,
                sequence_max_length,
                words_count,
                word_size,
                read_heads,
                batch_size
            )

            output, _ = ncomputer.get_outputs()

            loss_weights = tf.compat.v1.placeholder(tf.float32, [batch_size, None, 1])
            loss = tf.reduce_mean(
                loss_weights * tf.nn.softmax_cross_entropy_with_logits(logits=output, labels=ncomputer.target_output)
            )

            summaries = []

            gradients = optimizer.compute_gradients(loss)
            for i, (grad, var) in enumerate(gradients):
                if grad is not None:
                    gradients[i] = (tf.clip_by_value(grad, -10, 10), var)
            for (grad, var) in gradients:
                if grad is not None:
                    summaries.append(tf.compat.v1.summary.histogram(var.name + '/grad', grad))

            apply_gradients = optimizer.apply_gradients(gradients)

            summaries.append(tf.compat.v1.summary.scalar("Loss", loss))

            summarize_op = tf.compat.v1.summary.merge(summaries)
            no_summarize = tf.no_op()

            llprint("Done!\n")

            llprint("Initializing Variables ... ")
            session.run(tf.compat.v1.global_variables_initializer())
            llprint("Done!\n")

            if from_checkpoint is not None:
                llprint("Restoring Checkpoint %s ... " % from_checkpoint)
                ncomputer.restore(session, ckpts_dir, from_checkpoint)
                llprint("Done!\n")
            elif os.path.exists(ckpts_dir):
                checkpoints = os.listdir(ckpts_dir)
                if len(checkpoints) != 0 and any("step-" in s for s in checkpoints):
                    checkpoint_numbers = [int(checkpoint[checkpoint.find("-") + 1:]) for checkpoint in checkpoints if checkpoint[checkpoint.find("-") + 1:].isnumeric()]
                    checkpoint_numbers.sort()
                    ncomputer.restore(session, ckpts_dir, f"step-{checkpoint_numbers[-1]}")
                    start = checkpoint_numbers[-1]
                    end = 100000

            last_100_losses = []

            if not 'start' in locals():
                start = 0
                end = 100000
            if from_checkpoint is not None:
                start = int(from_checkpoint[from_checkpoint.find("-") + 1:])


            start_time_100 = time.time()
            end_time_100 = None
            avg_100_time = 0.
            avg_counter = 0

            for i in range(start, end + 1):
                try:
                    llprint("\rIteration %d/%d" % (i, end))

                    sample = np.random.choice(data_files, 1)
                    with open(os.path.join(data_dir, 'train', sample[0])) as f:
                        sample = json.load(f)
                    input_data, target_output, seq_len, weights = prepare_sample(sample, lexicon_dict['='],
                                                                                 word_space_size, lexicon_dict)

                    summarize = (i % 100 == 0)
                    take_checkpoint = (i != 0) and (i % 200 == 0)
                    #For debugging
                    outputs, _ = ncomputer.get_outputs()
                    softmaxed = tf.nn.softmax(outputs)

                    loss_value, _, summary, softmax_output = session.run([
                        loss,
                        apply_gradients,
                        summarize_op if summarize else no_summarize,
                        softmaxed
                    ], feed_dict={
                        ncomputer.input_data: input_data,
                        ncomputer.target_output: target_output,
                        ncomputer.sequence_length: seq_len,
                        loss_weights: weights
                    })
                    softmax_output = np.squeeze(softmax_output, axis=0)
                    given_answers = np.argmax(softmax_output, axis=1)

                    words = []
                    for an_array in target_output[0]:
                        for word in np.where(an_array == 1):
                            words.extend([list(lexicon_dict.keys())[np.where(an_array == 1)[0][0]]])

                    last_100_losses.append(loss_value)
                    if summarize:
                        print("\n\tLoss value: ", loss_value)
                        print("\tTarget output: ", words)
                        print("\tOutput: ", [list(lexicon_dict.keys())[num] for num in given_answers])
                        summarizer.add_summary(summary, i)
                        llprint("\tAvg. Cross-Entropy: %.7f\n" % (np.mean(last_100_losses)))

                        end_time_100 = time.time()
                        elapsed_time = (end_time_100 - start_time_100) / 60
                        avg_counter += 1
                        avg_100_time += (1. / avg_counter) * (elapsed_time - avg_100_time)
                        estimated_time = (avg_100_time * ((end - i) / 100.)) / 60.

                        print("\tAvg. 100 iterations time: %.2f minutes" % avg_100_time)
                        print("\tApprox. time to completion: %.2f hours\n" % estimated_time)

                        start_time_100 = time.time()
                        last_100_losses = []

                    if take_checkpoint:
                        llprint("\nSaving Checkpoint ... line 237 "),
                        ncomputer.save(session, ckpts_dir, 'step-%d' % i)
                        llprint("Done!\n")

                except KeyboardInterrupt:

                    llprint("\nSaving Checkpoint ... "),
                    ncomputer.save(session, ckpts_dir, 'step-%d' % i)
                    llprint("Done!\n")
                    sys.exit(0)


if __name__ == '__main__':
    main()
