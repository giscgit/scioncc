#!/usr/bin/env python

"""
Transport layer abstractions

TODOS:
- split listen() into two subcalls (for StreamSubscriber)
"""

__author__ = 'Dave Foster <dfoster@asascience.com>'
__license__ = 'Apache 2.0'

from pyon.util.log import log
from pyon.util.containers import DotDict
from gevent.event import AsyncResult
from gevent.queue import Queue
from gevent import coros
from contextlib import contextmanager
import os
from pika import BasicProperties

class TransportError(StandardError):
    pass

class BaseTransport(object):
    def declare_exchange_impl(self, client, exchange, **kwargs):
        raise NotImplementedError()
    def delete_exchange_impl(self, client, exchange, **kwargs):
        raise NotImplementedError()

    def declare_queue_impl(self, client, queue, **kwargs):
        raise NotImplementedError()
    def delete_queue_impl(self, client, queue, **kwargs):
        raise NotImplementedError()

    def bind_impl(self, client, exchange, queue, binding):
        raise NotImplementedError()
    def unbind_impl(self, client, exchange, queue, binding):
        raise NotImplementedError()

    def ack_impl(self, client, delivery_tag):
        raise NotImplementedError()
    def reject_impl(self, client, delivery_tag, requeue=False):
        raise NotImplementedError()

    def start_consume_impl(self, client, callback, queue, no_ack=False, exclusive=False):
        raise NotImplementedError()
    def stop_consume_impl(self, client, consumer_tag):
        raise NotImplementedError()

    def setup_listener(self, binding, default_cb):
        raise NotImplementedError()

    def get_stats_impl(self, client, queue):
        raise NotImplementedError()

    def purge_impl(self, client, queue):
        raise NotImplementedError()

    def qos_impl(self, client, prefetch_size=0, prefetch_count=0, global_=False):
        raise NotImplementedError()

    def publish_impl(self, client, exchange, routing_key, body, properties, immediate=False, mandatory=False): 
        raise NotImplementedError()

class AMQPTransport(BaseTransport):
    """
    This is STATELESS. You can make instances of it, but no need to (true singleton).
    """
    __instance = None

    @classmethod
    def get_instance(cls):
        if cls.__instance is None:
            cls.__instance = AMQPTransport()
        return cls.__instance

    @contextmanager
    def _push_close_cb(self, client, callback):
        client.add_on_close_callback(callback)
        try:
            yield callback
        finally:
            # PIKA BUG: v0.9.5, we need to specify the callback as a dict - this is fixed in git HEAD (13 Feb 2012)
            de = {'handle': callback, 'one_shot': True}
            client.callbacks.remove(client.channel_number, '_on_channel_close', de)

    def _sync_call(self, client, func, cb_arg, *args, **kwargs):
        """
        Functionally similar to the generic blocking_cb but with error support that's Channel specific.
        """
        ar = AsyncResult()

        def cb(*args, **kwargs):
            ret = list(args)
            if len(kwargs): ret.append(kwargs)
            ar.set(ret)

        eb = lambda ch, *args: ar.set(TransportError("_sync_call could not complete due to an error (%s)" % args))

        kwargs[cb_arg] = cb
        with self._push_close_cb(client, eb):
            func(*args, **kwargs)
            ret_vals = ar.get(timeout=10)

        if isinstance(ret_vals, TransportError):

            # mark this channel as poison, do not use again!
            # don't test for type here, we don't want to have to import PyonSelectConnection
            if hasattr(client.transport, 'connection') and hasattr(client.transport.connection, 'mark_bad_channel'):
                client.transport.connection.mark_bad_channel(client.channel_number)
            else:
                log.warn("Could not mark channel # (%s) as bad, Pika could be corrupt", client.channel_number)

            raise ret_vals

        if len(ret_vals) == 0:
            return None
        elif len(ret_vals) == 1:
            return ret_vals[0]
        return tuple(ret_vals)


    def declare_exchange_impl(self, client, exchange, exchange_type='topic', durable=False, auto_delete=True):
        log.debug("AMQPTransport.declare_exchange_impl(%s): %s, T %s, D %s, AD %s", client.channel_number, exchange, exchange_type, durable, auto_delete)
        arguments = {}

        if os.environ.get('QUEUE_BLAME', None) is not None:
            testid = os.environ['QUEUE_BLAME']
            arguments.update({'created-by':testid})

        self._sync_call(client, client.exchange_declare, 'callback',
                                             exchange=exchange,
                                             type=exchange_type,
                                             durable=durable,
                                             auto_delete=auto_delete,
                                             arguments=arguments)

    def delete_exchange_impl(self, client, exchange, **kwargs):
        log.debug("AMQPTransport.delete_exchange_impl(%s): %s", client.channel_number, exchange)
        self._sync_call(client, client.exchange_delete, 'callback', exchange=exchange)

    def declare_queue_impl(self, client, queue, durable=False, auto_delete=True):
        log.debug("AMQPTransport.declare_queue_impl(%s): %s, D %s, AD %s", client.channel_number, queue, durable, auto_delete)
        arguments = {}

        if os.environ.get('QUEUE_BLAME', None) is not None:
            testid = os.environ['QUEUE_BLAME']
            arguments.update({'created-by':testid})

        frame = self._sync_call(client, client.queue_declare, 'callback',
                                queue=queue or '',
                                auto_delete=auto_delete,
                                durable=durable,
                                arguments=arguments)

        return frame.method.queue

    def delete_queue_impl(self, client, queue, **kwargs):
        log.debug("AMQPTransport.delete_queue_impl(%s): %s", client.channel_number, queue)
        self._sync_call(client, client.queue_delete, 'callback', queue=queue)

    def bind_impl(self, client, exchange, queue, binding):
        log.debug("AMQPTransport.bind_impl(%s): EX %s, Q %s, B %s", client.channel_number, exchange, queue, binding)
        self._sync_call(client, client.queue_bind, 'callback',
                                        queue=queue,
                                        exchange=exchange,
                                        routing_key=binding)

    def unbind_impl(self, client, exchange, queue, binding):
        log.debug("AMQPTransport.unbind_impl(%s): EX %s, Q %s, B %s", client.channel_number, exchange, queue, binding)
        self._sync_call(client, client.queue_unbind, 'callback', queue=queue,
                                                     exchange=exchange,
                                                     routing_key=binding)

    def ack_impl(self, client, delivery_tag):
        """
        Acks a message.
        """
        log.debug("AMQPTransport.ack(%s): %s", client.channel_number, delivery_tag)
        client.basic_ack(delivery_tag)

    def reject_impl(self, client, delivery_tag, requeue=False):
        """
        Rejects a message.
        """
        client.basic_reject(delivery_tag, requeue=requeue)

    def start_consume_impl(self, client, callback, queue, no_ack=False, exclusive=False):
        """
        Starts consuming on a queue.
        Will asynchronously deliver messages to the callback method supplied.

        @return A consumer tag to be used when stop_consume_impl is called.
        """
        log.debug("AMQPTransport.start_consume_impl(%s): %s", client.channel_number, queue)
        consumer_tag = client.basic_consume(callback,
                                            queue=queue,
                                            no_ack=no_ack,
                                            exclusive=exclusive)
        return consumer_tag

    def stop_consume_impl(self, client, consumer_tag):
        """
        Stops consuming by consumer tag.
        """
        log.debug("AMQPTransport.stop_consume_impl(%s): %s", client.channel_number, consumer_tag)
        self._sync_call(client, client.basic_cancel, 'callback', consumer_tag)

    def setup_listener(self, binding, default_cb):
        """
        Calls setup listener via the default callback passed in.
        """
        return default_cb(self, binding)

    def get_stats_impl(self, client, queue):
        """
        Gets a tuple of number of messages, number of consumers on a queue.
        """
        log.debug("AMQPTransport.get_stats_impl(%s): Q %s", client.channel_number, queue)
        frame = self._sync_call(client, client.queue_declare, 'callback',
                                        queue=queue or '',
                                        passive=True)
        return frame.method.message_count, frame.method.consumer_count

    def purge_impl(self, client, queue):
        """
        Purges a queue.
        """
        log.debug("AMQPTransport.purge_impl(%s): Q %s", client.channel_number, queue)
        self._sync_call(client, client.queue_purge, 'callback', queue=queue)

    def qos_impl(self, client, prefetch_size=0, prefetch_count=0, global_=False):
        """
        Adjusts quality of service for a channel.
        """
        log.debug("AMQPTransport.qos_impl(%s): pf_size %s, pf_count %s, global_ %s", client.channel_number, prefetch_size, prefetch_count, global_)
        self._sync_call(client, client.basic_qos, 'callback', prefetch_size=prefetch_size, prefetch_count=prefetch_count, global_=global_)

    def publish_impl(self, client, exchange, routing_key, body, properties, immediate=False, mandatory=False):
        """
        Publishes a message on an exchange.
        """
        log.debug("AMQPTransport.publish(%s): ex %s key %s", client.channel_number, exchange, routing_key)

        props = BasicProperties(headers=properties)

        client.basic_publish(exchange=exchange, #todo
                             routing_key=routing_key, #todo
                             body=body,
                             properties=props,
                             immediate=immediate, #todo
                             mandatory=mandatory) #todo

class NameTrio(object):
    """
    Internal representation of a name/queue/binding (optional).
    Created and used at the Endpoint layer and sometimes Channel layer.
    """
    def __init__(self, exchange=None, queue=None, binding=None):
        """
        Creates a NameTrio.

        If either exchange or queue is a tuple, it will use that as a (exchange, queue, binding (optional)) triple.

        @param  exchange    An exchange name. You would typically use the sysname for that.
        @param  queue       Queue name.
        @param  binding     A binding/routing key (used for both recv and send sides). Optional,
                            and if not specified, defaults to the *internal* queue name.
        """
        if isinstance(exchange, tuple):
            self._exchange, self._queue, self._binding = list(exchange) + ([None] *(3-len(exchange)))
        elif isinstance(queue, tuple):
            self._exchange, self._queue, self._binding = list(queue) + ([None] *(3-len(queue)))
        else:
            self._exchange  = exchange
            self._queue     = queue
            self._binding   = binding

    @property
    def exchange(self):
        return self._exchange

    @property
    def queue(self):
        return self._queue

    @property
    def binding(self):
        return self._binding or self._queue

    def __str__(self):
        return "NP (%s,%s,B: %s)" % (self.exchange, self.queue, self.binding)


class LocalBroker(object):

    def __init__(self):
        self._exchanges = {}
        self._queues = {}
        self._binds = []        # list of tuples: exchange, queue, routing_key, who to call


        self._lock = coros.RLock()

    def incoming(self, exchange, routing_key, body, properties, immediate=False, mandatory=False):

        def binding_key_matches(bkey, rkey):
            return bkey == rkey # @TODO expand obv

        # find all matching calls
        matching_binds = [x for x in self._binds if x[0] == exchange and binding_key_matches(x[2], routing_key)]

        # make calls
        for bind in matching_binds:
            try:
                method_frame = DotDict()
                header_frame = DotDict()
                bind[3](self, method_frame, header_frame, body)
            except Exception as ex:
                log.exception("Error in local message routing, continuing")

        return True

    def declare_exchange(self, exchange, exchange_type='topic', durable=False, auto_delete=True):
        if exchange in self._exchanges:
            exrec = self._exchanges[exchange]

            assert exrec['type'] == exchange_type and exrec['durable'] == durable and exrec['auto_delete'] == auto_delete

        else:
            assert exchange_type == 'topic', "Topic only supported"

            self._exchanges[exchange] = { 'exchange': exchange,
                                          'type' : exchange_type,
                                          'durable' : durable,
                                          'auto_delete' : auto_delete }

        return True





class LocalTransport(BaseTransport):

    def __init__(self, broker):
        self._broker = broker

    def publish_impl(self, client, exchange, routing_key, body, properties, immediate=False, mandatory=False):
        return self._broker.incoming(exchange, routing_key, body, properties, immediate=immediate, mandatory=mandatory)

    def declare_exchange_impl(self, client, exchange, exchange_type='topic', durable=False, auto_delete=True):
        return self._broker.declare_exchange(exchange, exchange_type=exchange_type, durable=durable, auto_delete=auto_delete)







class TopicTrie(object):
    """
    Support class for building a zeromq device to do amqp-like pattern matching.

    Used for events/pubsub in our system with the zeromq transport. Efficiently stores all registered
    subscription topic trees in a trie structure, handling wildcards * and #.

    See:
        http://www.zeromq.org/whitepapers:message-matching      (doesn't handle # so scrapped)
        http://www.rabbitmq.com/blog/2010/09/14/very-fast-and-scalable-topic-routing-part-1/
        http://www.rabbitmq.com/blog/2011/03/28/very-fast-and-scalable-topic-routing-part-2/
    """

    class Node(object):
        """
        Internal node of a trie.

        Stores two data points: a token (literal string, '*', or '#', or None if used as root element),
                                and a set of "patterns" aka a ref to an object representing a bind.
        """
        def __init__(self, token, patterns=None):
            self.token = token
            self.patterns = patterns or []
            self.children = {}

        def get_or_create_child(self, token):
            """
            Returns a child node with the given token.

            If it doesn't already exist, it is created, otherwise the existing one is returned.
            """
            if token in self.children:
                return self.children[token]

            new_node = TopicTrie.Node(token)
            self.children[token] = new_node

            return new_node

        def get_all_matches(self, topics):
            """
            Given a list of topic tokens, returns all patterns stored in child nodes/self that match the topic tokens.

            This is a depth-first search pruned by token, with special handling for both wildcard types.
            """
            results = []

            if len(topics) == 0:
                # terminal point, return any pattern we have here
                return self.patterns

            cur_token = topics[0]
            rem_tokens = topics[1:]     # will always be a list, even if empty or 1-len
            log.debug('get_all_matches(%s): cur_token %s, rem_tokens %s', self.token, cur_token, rem_tokens)

            # child node direct matching
            if cur_token in self.children:
                results.extend(self.children[cur_token].get_all_matches(rem_tokens))

            # now '*' wildcard
            if '*' in self.children:
                results.extend(self.children['*'].get_all_matches(rem_tokens))

            # '#' means any number of tokens - naive method of descent, we'll feed it at least current token to start. Then chop
            # rem_tokens all the way down, put the results in a set to remove duplicates, and also any patterns on self.
            if '#' in self.children:
                # keep popping off and descend, make a set out of results
                all_wild_childs = set()
                for i in xrange(len(rem_tokens)):
                    res = self.children['#'].get_all_matches(rem_tokens[i:])
                    map(all_wild_childs.add, res)

                results.extend(all_wild_childs)
                results.extend(self.children['#'].patterns)     # any patterns defined in # are legal too

            return results

    def __init__(self):
        """
        Creates a dummy root node that all topic trees hang off of.
        """
        self.root = self.Node(None)

    def add_topic_tree(self, topic_tree, pattern):
        """
        Splits a string topic_tree into tokens (by .) and recursively adds them to the trie.

        Adds the pattern at the terminal node for later retrieval.
        """
        topics = topic_tree.split(".")

        curnode = self.root

        for topic in topics:
            curnode = curnode.get_or_create_child(topic)

        if not pattern in curnode.patterns:
            curnode.patterns.append(pattern)

    def remove_topic_tree(self, topic_tree, pattern):
        """
        Splits a string topic_tree into tokens (by .) and removes the pattern from the terminal node.

        @TODO should remove empty nodes
        """
        topics = topic_tree.split(".")

        curnode = self.root

        for topic in topics:
            curnode = curnode.get_or_create_child(topic)

        if pattern in curnode.patterns:
            curnode.patterns.remove(pattern)

    def get_all_matches(self, topic_tree):
        """
        Returns a list of all matches for a given topic tree string.

        Creates a set out of the matching patterns, so multiple binds matching on the same pattern only
        return once.
        """
        topics = topic_tree.split(".")
        return set(self.root.get_all_matches(topics))

from gevent_zeromq import zmq
from pyon.util.async import spawn
from pyon.util.pool import IDPool
from uuid import uuid4
from collections import defaultdict
import msgpack

class ZeroMQRouter(object):
    """
    A RabbitMQ-like routing device implemented with ZeroMQ.

    Using ZeroMQTransport, can handle topic-exchange-like communication in ION.
    """

    class ConsumerClosedMessage(object):
        """
        Dummy object used to exit queue get looping greenlets.
        """
        pass

    def __init__(self, context, sysname):
        self._context = context
        self._sysname = sysname

        # exchange/queues/bindings
        self._exchanges = {}                            # names -> { subscriber, topictrie(queue name) }
        self._queues = {}                               # names -> gevent queue
        self._bindings_by_queue = defaultdict(list)     # queue name -> [(ex, binding)]
        self._lock_declarables = coros.RLock()          # exchanges, queues, bindings, routing method

        # consumers
        self._consumers = defaultdict(list)             # queue name -> [ctag, channel._on_deliver]
        self._consumers_by_ctag = {}                    # ctag -> queue_name ??
        self._ctag_pool = IDPool()                      # pool of consumer tags
        self._lock_consumers = coros.RLock()            # lock for interacting with any consumer related attrs

        # deliveries
        self._unacked = {}                              # dtag -> (ctag, msg)
        self._lock_unacked = coros.RLock()              # lock for interacting with unacked field

        self._gl_msgs = None

        self.errors = []

    @property
    def _connect_addr(self):
        return "inproc://%s" % self._sysname

    def start(self):
        """
        Starts all internal greenlets of this router device.
        """
        self._sub = self._context.socket(zmq.SUB)
        self._sub.bind(self._connect_addr)
        self._sub.setsockopt(zmq.SUBSCRIBE, '')

        self._gl_msgs = spawn(self._run_gl_msgs)

    def stop(self):
        self._gl_msgs.kill()    # @TODO: better

    def _run_gl_msgs(self):
        while True:
            [ex, rkey, body, serprops] = self._sub.recv_multipart()
            try:
                props = msgpack.unpackb(serprops)

                with self._lock_declarables:
                    self._route(ex, rkey, body, props)
            except Exception as e:
                self.errors.append(e)
                log.exception("Routing message")

    def _route(self, exchange, routing_key, body, props):
        """
        Delivers incoming messages into queues based on known routes.

        This entire method runs in a lock (likely pretty slow).
        """
        assert exchange in self._exchanges, "Unknown exchange %s" % exchange

        queues = self._exchanges[exchange].get_all_matches(routing_key)
        log.debug("matched %s routes", len(queues))

        # deliver to each queue
        for q in queues:
            assert q in self._queues
            log.debug("deliver -> %s", q)
            self._queues[q].put((exchange, routing_key, body, props))

    def declare_exchange(self, exchange, **kwargs):
        with self._lock_declarables:
            if not exchange in self._exchanges:
                self._exchanges[exchange] = TopicTrie()

    def delete_exchange(self, exchange, **kwargs):
        with self._lock_declarables:
            if exchange in self._exchanges:
                del self._exchanges[exchange]

    def declare_queue(self, queue, **kwargs):

        with self._lock_declarables:
            # come up with new queue name if none specified
            if queue is None or queue == '':
                while True:
                    proposed = "q-%s" % str(uuid4())[0:10]
                    if proposed not in self._queues:
                        queue = proposed
                        break

            if not queue in self._queues:
                self._queues[queue] = Queue()

            return queue

    def delete_queue(self, queue, **kwargs):
        with self._lock_declarables:
            if queue in self._queues:
                del self._queues[queue]

                # kill bindings
                for ex, binding in self._bindings_by_queue[queue]:
                    if ex in self._exchanges:
                        self._exchanges[ex].remove_topic_tree(binding, queue)

    def bind(self, exchange, queue, binding):
        with self._lock_declarables:
            assert exchange in self._exchanges
            assert queue in self._queues

            self._exchanges[exchange].add_topic_tree(binding, queue)
            self._bindings_by_queue[queue].append((exchange, binding))

    def unbind(self, exchange, queue, binding):
        with self._lock_declarables:
            assert exchange in self._exchanges
            assert queue in self._queues

            self._exchanges[exchange].remove_topic_tree(binding, queue)
            for i, val in enumerate(self._bindings_by_queue[queue]):
                ex, b = val
                if ex == exchange and b == binding:
                    self._bindings_by_queue[queue].pop(i)
                    break

    def start_consume(self, callback, queue, no_ack=False, exclusive=False):
        assert queue in self._queues

        with self._lock_consumers:
            new_ctag = self._generate_ctag()
            assert new_ctag not in self._consumers_by_ctag

            with self._lock_declarables:
                gl = spawn(self._run_consumer, new_ctag, queue, self._queues[queue], callback)
            self._consumers[queue].append((new_ctag, callback, no_ack, exclusive, gl))
            self._consumers_by_ctag[new_ctag] = queue

            return new_ctag

    def stop_consume(self, consumer_tag):
        assert consumer_tag in self._consumers_by_ctag

        with self._lock_consumers:
            queue  = self._consumers_by_ctag[consumer_tag]
            self._consumers_by_ctag.pop(consumer_tag)

            for i, consumer in enumerate(self._consumers[queue]):
                if consumer[0] == consumer_tag:

                    # notify consumer greenlet that we want to stop
                    self._queues[queue].put(self.ConsumerClosedMessage())
                    consumer[4].join(timeout=5)
                    consumer[4].kill()

                    self._consumers[queue].pop(i)
                    break

            self._return_ctag(consumer_tag)

    def _run_consumer(self, ctag, queue_name, gqueue, callback):
        cnt = 0
        while True:
            m = gqueue.get()
            if isinstance(m, self.ConsumerClosedMessage):
                break
            exchange, routing_key, body, props = m

            # create method frame
            method_frame = DotDict()
            method_frame['consumer_tag']    = ctag
            method_frame['redelivered']     = False     # @TODO
            method_frame['exchange']        = exchange
            method_frame['routing_key']     = routing_key

            # create header frame
            header_frame = DotDict()
            header_frame['headers'] = props.copy()

            # make delivery tag for ack/reject later
            dtag = self._generate_dtag(ctag, cnt)
            cnt += 1

            with self._lock_unacked:
                self._unacked[dtag] = (ctag, queue_name, m)

            method_frame['delivery_tag'] = dtag

            # deliver to callback
            try:
                callback(self, method_frame, header_frame, body)
            except Exception:
                log.exception("delivering to consumer, ignore!")

    def _generate_ctag(self):
        return "zctag-%s" % self._ctag_pool.get_id()

    def _return_ctag(self, ctag):
        self._ctag_pool.release_id(int(ctag.split("-")[-1]))

    def _generate_dtag(self, ctag, cnt):
        """
        Generates a unique delivery tag for each consumer.

        Greenlet-safe, no need to lock.
        """
        return "%s-%s" % (ctag, cnt)

    def ack(self, delivery_tag):
        assert delivery_tag in self._unacked

        with self._lock_unacked:
            del self._unacked[delivery_tag]

    def reject(self, delivery_tag, requeue=False):
        assert delivery_tag in self._unacked

        with self._lock_unacked:
            _, queue, m = self._unacked.pop(delivery_tag)
            if requeue:
                log.warn("REQUEUE: EXPERIMENTAL %s", delivery_tag)
                self._queues[queue].append(m)

    def transport_close(self, transport):
        log.warn("ZeroMQRouter.transport_close: %s TODO", transport)

        # turn off any consumers from this transport

class ZeroMQTransport(BaseTransport):
    def __init__(self, broker, context):
        self._broker = broker
        self._context = context

        self._pub = self._context.socket(zmq.PUB)
        self._pub.connect(self._broker._connect_addr)

    def declare_exchange_impl(self, client, exchange, **kwargs):
        self._broker.declare_exchange(exchange, **kwargs)
    def delete_exchange_impl(self, client, exchange, **kwargs):
        self._broker.delete_exchange(exchange, **kwargs)

    def declare_queue_impl(self, client, queue, **kwargs):
        return self._broker.declare_queue(queue, **kwargs)
    def delete_queue_impl(self, client, queue, **kwargs):
        self._broker.delete_queue(queue, **kwargs)

    def bind_impl(self, client, exchange, queue, binding):
        self._broker.bind(exchange, queue, binding)
    def unbind_impl(self, client, exchange, queue, binding):
        self._broker.unbind(exchange, queue, binding)

    def publish_impl(self, client, exchange, routing_key, body, properties, immediate=False, mandatory=False):
        # properties is a dictionary, must serialize to send over zeromq
        propser = msgpack.packb(properties)
        self._pub.send_multipart([exchange, routing_key, body, propser])

    def start_consume_impl(self, client, callback, queue, no_ack=False, exclusive=False):
        return self._broker.start_consume(callback, queue, no_ack=no_ack, exclusive=exclusive)
    def stop_consume_impl(self, client, consumer_tag):
        self._broker.stop_consume(consumer_tag)

    def ack_impl(self, client, delivery_tag):
        self._broker.ack(delivery_tag)
    def reject_impl(self, client, delivery_tag, requeue=False):
        self._broker.reject(delivery_tag, requeue=requeue)

    def close(self):
        self._broker.transport_close(self)
