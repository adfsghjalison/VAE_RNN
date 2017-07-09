import tensorflow as tf
from ops import *
from utils import utils
import os
import sys
import numpy as np



class vrnn():
    
    def __init__(self,args,sess):
        self.sess = sess
        self.word_embedding_dim = 300
        self.drop_rate = 0.1
        self.num_epochs = 10000
        self.num_steps = args.num_steps
        self.latent_dim = args.latent_dim
        self.sequence_length = args.sequence_length
        self.batch_size = args.batch_size
        self.saving_step = args.saving_step
        self.model_dir = args.model_dir
        self.load_model = args.load
        self.lstm_length = [self.sequence_length+1]*self.batch_size
        self.utils = utils(args)
        self.vocab_size = len(self.utils.word_id_dict)

        self.EOS = 0
        self.BOS = 1
        self.log_dir = os.path.join(self.model_dir,'log/')
        self.build_graph()
        
        self.saver = tf.train.Saver(max_to_keep=2)
        self.model_path = os.path.join(self.model_dir,'model_{m_type}'.format(m_type='vrnn'))
 
    def build_graph(self):
        print('starting building graph')

        with tf.variable_scope("input") as scope:
            self.encoder_inputs = tf.placeholder(dtype=tf.int32, shape=(self.batch_size, self.sequence_length))
            self.train_decoder_sentence = tf.placeholder(dtype=tf.int32, shape=(self.batch_size, self.sequence_length))
            self.train_decoder_targets = tf.placeholder(dtype=tf.int32, shape=(self.batch_size, self.sequence_length))
            self.sample_rate = tf.placeholder(dtype=tf.float32)

            BOS_slice = tf.ones([self.batch_size, 1], dtype=tf.int32)*self.BOS
            EOS_slice = tf.ones([self.batch_size, 1], dtype=tf.int32)*self.EOS
            train_decoder_targets = tf.concat([self.train_decoder_targets,EOS_slice],axis=1)
            train_decoder_sentence = tf.concat([BOS_slice,self.train_decoder_sentence],axis=1)

        with tf.variable_scope("embedding") as scope:
            init = tf.contrib.layers.xavier_initializer()

            #word embedding
            word_embedding_matrix = tf.get_variable(
                name="word_embedding_matrix",
                shape=[self.vocab_size, self.word_embedding_dim],
                initializer=init,
                trainable = True)

            encoder_inputs_embedded = tf.nn.embedding_lookup(word_embedding_matrix, self.encoder_inputs)
            train_decoder_sentence_embedded = tf.nn.embedding_lookup(word_embedding_matrix, train_decoder_sentence)

        with tf.variable_scope("encoder") as scope:
            cell_fw = tf.contrib.rnn.LSTMCell(num_units=self.latent_dim, state_is_tuple=True)
            cell_bw = tf.contrib.rnn.LSTMCell(num_units=self.latent_dim, state_is_tuple=True)
            #bi-lstm encoder
            encoder_outputs, state = tf.nn.bidirectional_dynamic_rnn(
                cell_fw=cell_fw,
                cell_bw=cell_bw,
                dtype=tf.float32,
                sequence_length=self.lstm_length,
                inputs=encoder_inputs_embedded,
                time_major=False)

            output_fw, output_bw = encoder_outputs
            state_fw, state_bw = state
            encoder_outputs = tf.concat([output_fw,output_bw],2)      
            self.encoder_outputs=encoder_outputs
            encoder_state_c = tf.concat((state_fw.c, state_bw.c), 1)
            encoder_state_h = tf.concat((state_fw.h, state_bw.h), 1)
            self.encoder_state_c=encoder_state_c
            self.encoder_state_h=encoder_state_h
            encoder_state = tf.contrib.rnn.LSTMStateTuple(c=encoder_state_c, h=encoder_state_h) 
   
        decoder_inputs = batch_to_time_major(train_decoder_sentence_embedded ,self.sequence_length+1)  
        
        with tf.variable_scope("decoder") as scope:
       
            r_num = tf.reduce_sum(tf.random_uniform([1], seed=1))
            r_num = r_num
            cell = tf.contrib.rnn.LSTMCell(num_units=self.latent_dim*2, state_is_tuple=True)
            self.cell=cell

            def train_decoder_loop(prev,i):  
                prev_index = tf.stop_gradient(tf.argmax(prev, axis=-1))
                pred_prev = tf.nn.embedding_lookup(word_embedding_matrix, prev_index)
                next_input = tf.cond(r_num > self.sample_rate,\
                                lambda: pred_prev,\
                                lambda: decoder_inputs[i] ) #r>rate do first, else second
                return next_input


            def test_decoder_loop(prev,i):
                prev_index = tf.stop_gradient(tf.argmax(prev, axis=-1))
                pred_prev = tf.nn.embedding_lookup(word_embedding_matrix, prev_index)
                next_input = pred_prev
                return next_input


            #the decoder of training
            train_decoder_output,train_decoder_state = tf.contrib.legacy_seq2seq.attention_decoder(
                decoder_inputs = decoder_inputs,
                initial_state = encoder_state,
                attention_states = encoder_outputs,
                cell = cell,
                output_size = self.vocab_size,
                loop_function = train_decoder_loop,
                scope = scope
            )
            
            #the decoder of testing
            scope.reuse_variables()
            test_decoder_output,test_decoder_state = tf.contrib.legacy_seq2seq.attention_decoder(
                decoder_inputs = decoder_inputs,
                initial_state = encoder_state,
                attention_states = encoder_outputs,
                cell = cell,
                output_size = self.vocab_size,
                loop_function = test_decoder_loop,
                scope = scope
            )   #the test decoder input can be same as train

            
            test_decoder_logits = tf.stack(test_decoder_output, axis=1)
            test_pred = tf.argmax(test_decoder_logits,axis=-1)
            test_pred = tf.to_int32(test_pred,name='ToInt32')

            self.test_pred=test_pred
        
        #seq2seq
        #train_decoder_output,test_pred = peeky_seq2seq(
        #   encoder_inputs=encoder_inputs_embedded,
        #    decoder_inputs=train_decoder_sentence_embedded,
        #    peeky_code=train_decoder_character_embedded,
        #    word_embedding_matrix=word_embedding_matrix,
        #    vocab_size=self.vocab_size,
        #    sequence_length=self.sequence_length,
        #    latent_dim=self.latent_dim,
        #    encoder_length=self.lstm_length,s
        #    sample_rate=self.sample_rate,
        #    peeky_code_dim=self.character_embedding_dim
        #)
        #self.test_pred = test_pred

        with tf.variable_scope("loss") as scope:
            targets = batch_to_time_major(train_decoder_targets,self.sequence_length+1)
            loss_weights = [tf.ones([self.batch_size],dtype=tf.float32) for _ in range(self.sequence_length+1)]    #the weight at each time step
            self.loss = tf.contrib.legacy_seq2seq.sequence_loss(
                logits = train_decoder_output, 
                targets = targets,
                weights = loss_weights)
            #self.train_op = tf.train.RMSPropOptimizer(0.001).minimize(self.loss)
            self.train_op = tf.train.AdamOptimizer().minimize(self.loss)
            tf.summary.scalar('total_loss', self.loss)

    
    def train(self):
        summary = tf.summary.merge_all()
        summary_writer = tf.summary.FileWriter(self.log_dir, self.sess.graph)
        saving_step = self.saving_step
        summary_step = saving_step/10
        cur_loss = 0.0
        
        if self.load_model:
            self.saver.restore(self.sess, tf.train.latest_checkpoint(self.model_dir))
        else:
            self.sess.run(tf.global_variables_initializer())
        step = 0
        
        for s,t in self.utils.train_data_generator(self.num_epochs):
            step += 1
            t_d = t
            sample_rate = max(0.5,0.9-(step/100)*0.001)
            #self.drop_rate = min((step/100)*0.001,0.7)
            #t_d = self.utils.word_drop_out(t,self.drop_rate)
            feed_dict = {
                self.encoder_inputs:s,\
                self.train_decoder_sentence:t_d,\
                self.train_decoder_targets:t,\
                self.sample_rate:sample_rate
            }
            _,loss = self.sess.run([self.train_op, self.loss],feed_dict)
            #print('r: ',r_num , sample_rate , ' loss: ' , loss)
            cur_loss += loss
            if step%(summary_step)==0:
                print('{step}: total_loss: {loss}'.format(step=step,loss=cur_loss/summary_step))
                cur_loss = 0.0
            if step%saving_step==0:
                self.saver.save(self.sess, self.model_path, global_step=step)
            if step>=self.num_steps:
                break
                
                
    def stdin_test(self):
        sentence = 'hi'
        self.saver.restore(self.sess, tf.train.latest_checkpoint(self.model_dir))
		
        #new_saver = tf.train.import_meta_graph('./senti_model/sent_cls.ckpt.meta',clear_devices=True)
        #new_saver.restore(self.sess, './senti_model/sent_cls.ckpt')
        while(sentence):
            sentence = sys.stdin.readline()
            sys.stdout.flush()
            input_sent_vec = self.utils.sent2id(sentence)
            sent_vec = np.zeros((self.batch_size,self.sequence_length),dtype=np.int32)
            sent_vec[0] = input_sent_vec
            t = np.ones((self.batch_size,self.sequence_length),dtype=np.int32)
            feed_dict = {
                    self.encoder_inputs:sent_vec,\
                    self.train_decoder_sentence:t
            }
            preds = self.sess.run([self.test_pred],feed_dict)
            pred_sent = self.utils.id2sent(preds[0][0])
            print(pred_sent)            