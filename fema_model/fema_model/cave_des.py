from pamda import pamda_core

class Utils(pamda_core):
    def get_pct(self, numerator, denominator):
        if denominator==0:
            return 1
        else:
            return numerator/denominator

    def round_sum(self, data, decimal_places=None):
        if decimal_places==None:
            decimal_places=self.decimal_places
        return self.hardRound(decimal_places=decimal_places, a=sum(data))

class Queue:
    def __init__(self, max_queue_length=None):
        self.queue=[]
        self.max_queue_length=max_queue_length

    def add(self, object):
        if object in self.queue:
            raise ValueError("Object is already in the queue")
        self.queue.append(object)

    def remove(self):
        object=self.queue[0]
        self.queue=self.queue[1:]
        return object

    def queue_length(self):
        return len(self.queue)

    def has_queue(self):
        if self.queue_length()>0:
            return True
        else:
            return False

    def has_queue_space(self):
        if self.max_queue_length:
            if self.queue_length()>=self.max_queue_length:
                return False
        return True

class Time_Object(Utils):
    def __init__(self, id, starting_event_time=0, open_time=0, close_time=24, final_event_time=24, decimal_places=3, **kwargs):
        self.decimal_places=decimal_places
        self.id=id
        self.next_event_time=starting_event_time
        self.current_event_time=starting_event_time
        self.final_event_time=final_event_time
        self.open_time=self.hardRound(decimal_places=self.decimal_places, a=open_time)
        self.close_time=self.hardRound(decimal_places=self.decimal_places, a=close_time)
        self.event=None
        self.waiting=False
        self.stat_data={}
        self.log=[]
        self.statistics={}
        self.closed=True
        self.will_reopen=True
        self.gen_event('not_open',self.open_time)

    def gen_event(self, event, time_cost, log_info=None):
        # When adding an event, assume the last next event has just finished
        self.current_event_time=self.next_event_time
        # Calculate the time at which the next event will happen
        time_cost=self.hardRound(a=time_cost, decimal_places=self.decimal_places)

        self.next_event_time=self.hardRound(a=self.current_event_time+time_cost, decimal_places=self.decimal_places)
        # Special logic to handle events that time out past or during the close time
        if self.next_event_time>=self.close_time:
            self.next_event_time=self.current_event_time
            self.close_object(log_info=log_info)
        else:
            self.event=event
            self.add_to_stat_data(event, time_cost)
            self.add_to_log(event, self.current_event_time, self.next_event_time, log_info)

    def synch_gen_event(self, event, time_cost, synch_obj=None, log_info=None):
        self.gen_event(
            event=event,
            time_cost=time_cost,
            log_info=log_info
        )
        if not self.closed and synch_obj is not None:
            synch_obj.gen_event(
                event=event,
                time_cost=time_cost,
                log_info=log_info
            )

    def close_object(self, event='out_of_time', log_info=None, will_reopen=False):
        time_cost=self.hardRound(a=self.close_time-self.next_event_time, decimal_places=self.decimal_places)
        self.add_to_stat_data(event, time_cost)
        self.add_to_log(event, self.current_event_time, self.close_time, log_info)

        time_cost=self.hardRound(a=self.final_event_time-self.close_time, decimal_places=self.decimal_places)
        self.add_to_stat_data('not_open', time_cost)
        self.add_to_log('not_open', self.close_time, self.final_event_time, log_info)

        self.event='not_open'
        self.next_event_time=self.final_event_time
        self.current_event_time=self.final_event_time
        self.closed=True
        self.will_reopen=will_reopen

    def add_to_stat_data(self, event, time_cost):
        if not isinstance(self.stat_data.get(event, 0), list):
            self.stat_data[event]=[]
        self.stat_data[event].append(time_cost)

    def get_statistics(self):
        def serialize(data):
            return {
                'total': sum(data),
                'count': len(data),
                'mean': sum(data)/len(data),
                'max': max(data),
                'min': min(data)
            }
        self.statistics['events']={k:serialize(v) for k, v in self.stat_data.items()}

    def add_to_log(self, event, start_time, end_time, log_info):
        self.log.append({
            'event':event,
            'start_time':start_time,
            'end_time':end_time,
            'log_info':log_info
        })

    def wait_event(self, event, time_cost, log_info=None):
        event_now=self.event
        self.gen_event(event, time_cost, log_info)
        if not self.closed:
            self.event=event_now

    def synch_wait_event(self, event, time_cost, synch_obj=None, log_info=None):
        event_now=self.event
        if synch_obj is not None:
            synch_event_now=synch_obj.event
        self.synch_gen_event(event, time_cost, synch_obj, log_info)
        if not self.closed:
            self.event=event_now
        if synch_obj is not None:
            if not synch_obj.closed:
                synch_obj.event=synch_event_now

    def start_waiting(self, time):
        if self.waiting:
            raise ValueError("Object is already waiting")
        self.waiting=True
        self.last_used_time=time

    def stop_waiting(self, time, event):
        if not self.waiting:
            raise ValueError("Object is already not waiting")
        if time<self.last_used_time:
            raise ValueError('Stop waiting times must be after the last start waiting time.')
        self.waiting=False
        time_cost=time-self.last_used_time
        self.gen_event(event, time_cost)

    def next_event(self):
        if self.open_time==self.next_event_time:
            self.closed=False

class Queued_Object(Time_Object):
    def __init__(self, queue=None, **kwargs):
        super().__init__(**kwargs)
        if queue==None:
            queue=Queue()
        self.queue=queue
        self.in_process_obj=None

    def add_to_queue(self, object, event='no_queue'):
        if self.waiting:
            self.stop_waiting(object.next_event_time, event)
        object.start_waiting(object.next_event_time)
        self.queue.add(object)

    def pull_from_queue(self, time, object_event=None):
        if object_event is None:
            object_event='in_{class_name}_queue'.format(class_name=self.__class__.__name__.lower())
        stop=False
        object=self.queue.remove()
        object.stop_waiting(time, object_event)
        return object

    def next_event(self):
        super().next_event()
        if self.queue.has_queue():
            self.process_queue()
        else:
            self.start_waiting(self.next_event_time)

    def open_object(self, event='not_open'):
        super().open_object(event)
        self.start_waiting(time=self.open_time)

    def close_object(self, event='out_of_time', log_info=None, will_reopen=False, fx_on_kick=None):
        if self.in_process_obj is not None:
            self.in_process_obj.gen_event('kicked_from_{}_process'.format(self.__class__.__name__.lower()),0)
            if fx_on_kick is not None:
                fx_on_kick(self.in_process_obj)
        if self.queue.has_queue():
            for i in range(self.queue.queue_length()):
                x=self.pull_from_queue(self.close_time)
                x.gen_event('kicked_from_{}_queue'.format(self.__class__.__name__.lower()),0)
                if fx_on_kick is not None:
                    fx_on_kick(x)
        super().close_object(event=event, log_info=log_info, will_reopen=will_reopen)
