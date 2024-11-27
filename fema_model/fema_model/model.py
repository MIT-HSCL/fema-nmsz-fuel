import random
from fema_model.cave_des import Time_Object, Queued_Object, Utils, Queue

class Iterating_Time_Object(Time_Object):
    def __init__(self, **kwargs):
        self.new_iteration()
        super().__init__(**kwargs)

    def new_iteration(self):
        self.iterating_stat_data={}

    def add_to_stat_data(self, event, time_cost):
        if not isinstance(self.iterating_stat_data.get(event, 0), list):
            self.iterating_stat_data[event]=[]
        self.iterating_stat_data[event].append(time_cost)
        super().add_to_stat_data(event, time_cost)

class Random_Open_Time_Object:
    def __init__(self, open_time=0, close_time=24, **kwargs):
        """
        A special init function to handle kwarg unpacking and random open/close time assignment on classes where this class is inherited as the next item in the MRO
        """
        def clamp(min_val, max_val, val):
            return min(max(min_val,val),max_val)
        open_time_min=clamp(0, 24, kwargs.pop('open_time_min', open_time))
        open_time_max=clamp(open_time_min, 24, kwargs.pop('open_time_max', open_time))
        close_time_after_open=clamp(0, 24-open_time_max, kwargs.pop('close_time_after_open',close_time))
        close_time_min=clamp(open_time_max, 24, kwargs.pop('close_time_min', close_time))
        close_time_max=clamp(close_time_min, 24, kwargs.pop('close_time_max', close_time))
        kwargs['open_time']=random.uniform(open_time_min, open_time_max)
        if close_time_after_open:
            kwargs['close_time']=open_time+close_time_after_open
        else:
            kwargs['close_time']=random.uniform(close_time_min, close_time_max)
        super().__init__(**kwargs)

class Iterating_Queue_Object(Queued_Object):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.iteration_stats={}

    def add_iteration_stats(self, truck, extra_events=[]):
        relevant_events=[i for i in truck.iterating_stat_data if self.__class__.__name__.lower() in i]+extra_events
        for event in relevant_events:
            if not isinstance(self.iteration_stats.get(event, 0), list):
                self.iteration_stats[event]=[]
            self.iteration_stats[event].append(sum(truck.iterating_stat_data[event]))

    def get_statistics(self):
        super().get_statistics()
        def serialize(data):
            return {
                'total': sum(data),
                'count': len(data),
                'mean': sum(data)/max(len(data),1),
                'max': max(data),
                'min': min(data)
            }
        self.statistics['iter_events']={k:serialize(v) for k, v in self.iteration_stats.items()}

class Bay(Iterating_Queue_Object):
    # SR Edit
    # fill_rate = mode mins at bay, fill_rate_sigma ~ bounds of triangular distribution
    def __init__(self, fuel_types, fill_rate=26, fill_rate_sigma=2, fill_rate_min=0, **kwargs):
        super().__init__(**kwargs)
        self.fuel_types=fuel_types
        self.fuel_supplied=0
        self.trucks_serviced=0
        self.fill_rate=fill_rate
        self.fill_rate_sigma=fill_rate_sigma
        self.fill_rate_min=fill_rate_min

    def close_object(self, event='out_of_time', log_info=None, will_reopen=False):
        super().close_object(event=event, log_info=None, will_reopen=False, fx_on_kick=self.add_iteration_stats)

    def process_queue(self):
        self.in_process_obj=self.pull_from_queue(self.next_event_time)
        self.in_process_obj.route_bay=self
        fill_time=self.in_process_obj.calc_fill_time()
        self.synch_gen_event(event='bay_fill_truck', time_cost=fill_time, synch_obj=self.in_process_obj)
        if not self.closed:
            if not self.in_process_obj.closed:
                self.fuel_supplied+=self.in_process_obj.route_station_amt_of_fuel_to_service
                self.trucks_serviced+=1
            self.add_iteration_stats(self.in_process_obj)
        self.in_process_obj=None

    def get_statistics(self):
        super().get_statistics()
        self.statistics['fuel_supplied']=self.fuel_supplied
        self.statistics['trucks_serviced']=self.trucks_serviced

class Gate(Iterating_Queue_Object):
    # bays is the list of bay objects that this Gate can process to
    # SR Edit
    # gate_rate = mode mins at gate, gate_rate_sigma ~ bounds of triangular distribution
    def __init__(self, bays, gate_rate=4, gate_rate_sigma=0.5, gate_rate_min=0, **kwargs):
        super().__init__(**kwargs)
        self.bays=bays
        self.gate_rate=gate_rate
        self.gate_rate_sigma=gate_rate_sigma
        self.gate_rate_min=gate_rate_min
        self.fuel_types=self.get_fuel_types()
        self.trucks_serviced=0

    def get_fuel_types(self):
        return list(set([i for j in self.bays for i in j.fuel_types]))

    def calc_process_time(self):
        # SR Edit
        # pick time in mins from triangular distribution -- convert to hrs
        return (random.triangular(self.gate_rate-self.gate_rate_sigma, self.gate_rate+self.gate_rate_sigma, self.gate_rate)/60)

    def process_queue(self):
        self.in_process_obj=self.pull_from_queue(self.next_event_time)
        self.in_process_obj.route_gate=self
        process_time=self.calc_process_time()
        self.synch_gen_event(event='gate_processing', time_cost=process_time, synch_obj=self.in_process_obj)
        if not self.closed:
            if not self.in_process_obj.closed:
                self.trucks_serviced+=1
            self.add_iteration_stats(self.in_process_obj)

    def next_event(self):
        if self.in_process_obj is not None:
            if not self.in_process_obj.closed:
                route_bay=self.in_process_obj.join_shortest_matching_queue(
                    obj_list=self.bays,
                    synch_obj=self,
                    check_for_queue_space=True
                )
                if route_bay is None and self.in_process_obj.event=="gate_processing":
                    return
            self.in_process_obj=None
        super().next_event()

    def close_object(self, event='out_of_time', log_info=None, will_reopen=False):
        super().close_object(event=event, log_info=None, will_reopen=False, fx_on_kick=self.add_iteration_stats)

class Station(Iterating_Queue_Object):
    # distance is the distance from the terminal to the station in miles
    # demand is the number of gallons demanded in a day
    def __init__(self, data, distance, new_demand, carry_over_demand, demand, geo_code, fuel_types, name=None, **kwargs):
        super().__init__(**kwargs)
        self.name=name
        self.data=data
        self.distance=distance
        self.starting_demand=demand
        self.demand_after_inbound_service=demand
        self.new_demand=new_demand
        self.carry_over_demand=carry_over_demand
        self.demand=demand
        self.geo_code=geo_code
        self.times_serviced=0
        self.times_serviced_plus_inbound=0
        self.fuel_types=fuel_types
        # Ensure stations never have shared queues
        self.queue=Queue()

    def process_queue(self):
        self.in_process_obj=self.pull_from_queue(self.next_event_time)
        empty_time=self.in_process_obj.calc_empty_time()
        self.synch_gen_event(event='station_empty_truck', time_cost=empty_time, synch_obj=self.in_process_obj)
        if not self.closed:
            if not self.in_process_obj.closed:
                fuel_supplied=self.in_process_obj.route_station_amt_of_fuel_to_service
                self.demand-=fuel_supplied
                self.times_serviced+=1
                self.in_process_obj.completed_a_delivery=True
                self.in_process_obj.fuel_delivered+=fuel_supplied
                self.add_iteration_stats(self.in_process_obj)
            else:
                self.unmark_for_service(self.in_process_obj)
        self.in_process_obj=None

    def mark_for_service(self, truck):
        self.times_serviced_plus_inbound+=1
        truck.route_station_amt_of_fuel_to_service=min(truck.size, self.demand_after_inbound_service)
        self.demand_after_inbound_service-=truck.route_station_amt_of_fuel_to_service

    def unmark_for_service(self, truck):
        self.demand_after_inbound_service+=truck.route_station_amt_of_fuel_to_service

    def get_statistics(self):
        super().get_statistics()
        total_demand=self.starting_demand
        met_total_demand=self.starting_demand-max(self.demand,0)
        met_new_demand=min(met_total_demand, self.new_demand)
        met_carry_over_demand=min(max(met_total_demand-self.new_demand,0),self.carry_over_demand)
        self.statistics['total_demand']=total_demand
        self.statistics['new_demand']=self.new_demand
        self.statistics['carry_over_demand']=self.carry_over_demand

        self.statistics['met_total_demand']=met_total_demand
        self.statistics['met_new_demand']=met_new_demand
        self.statistics['met_carry_over_demand']=met_carry_over_demand
        self.statistics['unmet_total_demand']=total_demand-met_total_demand

        # Used for UI purposes when grouping stations
        self.statistics['count_stations']=1

        self.statistics['pct_new_demand_met']=self.hardRound(
            a=self.get_pct(met_new_demand, self.new_demand),
            decimal_places=self.decimal_places
        )
        self.statistics['pct_carry_over_demand_met']=self.hardRound(
            a=self.get_pct(met_carry_over_demand, self.carry_over_demand),
            decimal_places=self.decimal_places
        )
        self.statistics['pct_demand_met']=self.hardRound(
            a=self.get_pct(met_total_demand, total_demand),
            decimal_places=self.decimal_places
        )
        for key, value in self.data.items():
            if key not in ['geo_code', 'latitude', 'longitude', 'name', 'demand_scenarios']:
                self.statistics[key]=value

    def serialize(self, minify=False):
        return {
            'id':self.id,
            'latitude':self.data.get('latitude',0),
            'longitude':self.data.get('longitude',0),
            'statistics': {k:v for k, v in self.statistics.items() if k!='events' and k!='iter_events'},
            'name': self.name
            }

class Truck(Random_Open_Time_Object, Iterating_Time_Object):
    # size is in gallons
    # gates is the list of current gates for this truck
    # stations is the list of stations for this truck
    # SR Edit
    # speed = mode mph, speed_sigma ~ bounds of triangular distribution
    # empty_rate = mode min at station, empty_rate_sigma ~ bounds of triangular distribution
    # close_early = close truck if 30 mins left -- can't reach station
    def __init__(self, initial_distance_to_terminal_group, gates, stations, size=9000, speed=45, speed_sigma=5, speed_min=0, empty_rate=60, empty_rate_sigma=10, empty_rate_min=0, wait_event_duration=.1, station_algorithm='max', close_early_delta=0.5, **kwargs):
        super().__init__(**kwargs)
        self.size=size
        self.speed=speed
        self.speed_sigma=speed_sigma
        self.speed_min=speed_min
        self.gates=gates
        self.stations=stations
        self.empty_rate=empty_rate
        self.empty_rate_sigma=empty_rate_sigma
        self.empty_rate_min=empty_rate_min
        self.wait_event_duration=wait_event_duration
        self.station_algorithm=station_algorithm
        self.initial_distance_to_terminal_group=initial_distance_to_terminal_group
        self.close_early_delta=close_early_delta
        self.fuel_delivered=0
        self.completed_a_delivery=False

    def calc_travel_time(self, distance):
        # SR Edit
        # pick speed from triangular distribution
        return (distance/random.triangular(self.speed-self.speed_sigma, self.speed+self.speed_sigma, self.speed))

    def calc_fill_time(self):
        # SR Edit
        # pick time in mins from triangular distribution -- convert to hrs
        return (random.triangular(self.route_bay.fill_rate-self.route_bay.fill_rate_sigma, self.route_bay.fill_rate+self.route_bay.fill_rate_sigma, self.route_bay.fill_rate)/60)
    
    def calc_empty_time(self):
        # SR Edit
        # pick time in mins from triangular distribution -- convert to hrs
        return (random.triangular(self.empty_rate-self.empty_rate_sigma, self.empty_rate+self.empty_rate_sigma, self.empty_rate)/60)

    def pick_station(self):
        stations_to_reopen=self.get_will_reopen(self.stations)
        if len(stations_to_reopen)==0:
            self.close_object()
            return
        stations=[i for i in stations_to_reopen if i.demand_after_inbound_service>0]
        if len(stations)==0:
            self.close_object()
            return
        if self.station_algorithm=='max':
            self.route_station = max(stations, key=lambda x: x.demand_after_inbound_service)
        elif self.station_algorithm=='cycle_demand_hi_low':
            self.route_station = self.head(sorted(stations, key=lambda x: (x.times_serviced_plus_inbound, -x.demand_after_inbound_service)))
        elif self.station_algorithm=='cycle_demand_low_hi':
            self.route_station = self.head(sorted(stations, key=lambda x: (x.times_serviced_plus_inbound, x.demand_after_inbound_service)))
        elif self.station_algorithm=='cycle_distance_hi_low':
            self.route_station = self.head(sorted(stations, key=lambda x: (x.times_serviced_plus_inbound, -x.distance)))
        elif self.station_algorithm=='cycle_distance_low_hi':
            self.route_station = self.head(sorted(stations, key=lambda x: (x.times_serviced_plus_inbound, x.distance)))
        self.route_station.mark_for_service(self)

    def join_shortest_matching_queue(self, obj_list, check_for_queue_space=False, synch_obj=None):
        matching_fuel_obj_lst=self.get_matching_fuel_objects(self.get_open(obj_list))
        if check_for_queue_space:
            matching_obj_lst=self.get_objects_with_queue_space(matching_fuel_obj_lst)
        else:
            matching_obj_lst=matching_fuel_obj_lst
        if len(matching_obj_lst)>0:
            # Join the shortest matching Queued_Object given its Queue (or shared queue)
            # If a shared Queue, prioritize Queued_Objects that are waiting
            # This will force the Queued_Object to stop waiting and start serving
            obj=min(matching_obj_lst, key=lambda x: (x.queue.queue_length(), -x.waiting))
            obj.add_to_queue(self)
            return obj
        elif len(matching_fuel_obj_lst)==0:
            if len(self.get_matching_fuel_objects(self.get_will_reopen(obj_list)))==0:
                self.drop_station()
                return None
            event='wait_for_{}_to_open'.format(obj_list[0].__class__.__name__.lower())
            self.synch_wait_event(event, self.wait_event_duration, synch_obj=synch_obj)
            return None
        else:
            event='wait_for_{}_queue_space'.format(obj_list[0].__class__.__name__.lower())
            self.synch_wait_event(event, self.wait_event_duration, synch_obj=synch_obj)
            return None

    def get_open(self, obj_list):
        if len(obj_list)>0:
            return [i for i in obj_list if not i.closed]
        return obj_list

    def get_will_reopen(self, obj_list):
        if len(obj_list)>0:
            return [i for i in self.stations if i.will_reopen]
        return obj_list

    def get_matching_fuel_objects(self, obj_list):
        if len(obj_list)>0:
            station_fuels=set(self.route_station.fuel_types)
            return [i for i in obj_list if len(set(i.fuel_types).intersection(station_fuels))>0]
        return []

    def get_objects_with_queue_space(self, obj_list):
        if len(obj_list)>0:
            return [i for i in obj_list if i.queue.has_queue_space()]
        return obj_list

    def reset_route_items(self):
        self.new_iteration()
        self.route_gate=None
        self.route_bay=None
        self.route_station=None

    def drop_station(self):
        self.route_station.unmark_for_service(self)
        if 'station' in self.event:
            travel_time=self.calc_travel_time(self.route_station.distance)
        else:
            travel_time=0
        self.gen_event('to_terminal_group',travel_time)

    def next_event(self):
        super().next_event()
        if self.event=='to_terminal_group':
            self.reset_route_items()
            if self.next_event_time+self.close_early_delta>self.close_time:
                self.close_object(event='close_early')
            else:
                self.pick_station()
                if self.route_station is not None:
                    self.route_gate=self.join_shortest_matching_queue(obj_list=self.gates)
        # NOTE: Route Gates fire the event to determine when to move the truck to the bay since queued objects always execute events first
        # This allows the route gate to synchronize their wait with the truck if matching fuel bay queues are full
        elif self.event=='bay_fill_truck':
            self.gen_event('to_station', self.calc_travel_time(self.route_station.distance))
        elif self.event=='to_station':
            self.join_shortest_matching_queue(obj_list=[self.route_station])
        elif self.event=='station_empty_truck':
            self.gen_event('to_terminal_group', self.calc_travel_time(self.route_station.distance))
        elif self.event=='not_open' and self.will_reopen:
            self.gen_event('to_terminal_group', self.calc_travel_time(self.initial_distance_to_terminal_group))
        elif 'kicked_from' in self.event:
            # If kicked from any queue because it closes, assume fuel is dumped instantly and the truck goes back to the terminal group
            if 'station' in self.event:
                travel_time=self.calc_travel_time(self.route_station.distance)
            else:
                travel_time=0
            self.gen_event('to_terminal_group', travel_time)
        else:
            raise ValueError("Unrecognized Event:{}".format(self.event))

    def get_statistics(self):
        super().get_statistics()
        self.statistics['fuel_delivered']=self.fuel_delivered

class Flag():
    def __init__(self, data):
        self.data=data
        self.id=self.data.get('id',0)
        self.statistics={}

    def get_statistics(self):
        for key, value in self.data.items():
            if key not in ['latitude', 'longitude', 'name', 'id']:
                self.statistics[key]=value

    def serialize(self, minify=False):
        self.get_statistics()
        return {
            'id':self.data.get('id',0),
            'latitude':self.data.get('latitude',0),
            'longitude':self.data.get('longitude',0),
            'statistics': self.statistics,
            'name': self.data.get('name', 'No Name Provided')
            }

class Simulate:
    def __init__(self, all_objects):
        self.all_objects=all_objects
        self.run_sim()

    def run_sim(self):
        stop=False
        while not stop:
            objects=[x for x in self.all_objects if not x.waiting]
            if len(objects)>0:
                object=min(objects, key=lambda x: (x.next_event_time, not isinstance(x, Queued_Object)))
            else:
                stop=True
            if not stop:
                if object.next_event_time>=object.final_event_time:
                    stop=True
                else:
                    object.next_event()

        close_out=[x for x in self.all_objects if x.waiting]
        for object in close_out:
            object.close_object()
        for object in self.all_objects:
            object.get_statistics()

class Geo_Codes(Utils):
    def __init__(self, geo_areas):
        self.geo_areas=geo_areas
        self.statistics={}
        self.decimal_places=3
        self.create_geos()

    def create_geos(self):
        self.geos={}
        for key, value in self.geo_areas.items():
            self.geos[str(key)]=[]

    def add_object(self, object):
        if object.geo_code not in self.geos:
            self.geos[object.geo_code]=[]
        self.geos[object.geo_code].append(object)

    def get_statistics(self):
        for geo_code in self.geos:
            objects=self.geos[geo_code]
            geo_stats=['total_demand', 'met_total_demand', 'new_demand', 'met_new_demand', 'carry_over_demand', 'met_carry_over_demand']
            geo_code_statistics={stat:sum([x.statistics[stat] for x in objects]) for stat in geo_stats}
            for key, value in self.geo_areas.get(str(geo_code),{}).items():
                geo_code_statistics[key]=value
            geo_code_statistics['area']=self.hardRound(a=geo_code_statistics.get('area',0), decimal_places=self.decimal_places)

            per_area_stats=['total_demand', 'new_demand', 'carry_over_demand']
            for stat in per_area_stats:
                # Demand Per Area
                geo_code_statistics['{}_per_area'.format(stat)]=self.hardRound(
                    a=geo_code_statistics[stat]/max(geo_code_statistics['area'],1),
                    decimal_places=1
                )
                # Pct Demand Filled Per area
                geo_code_statistics['pct_{}_met'.format(stat)]=self.hardRound(
                    a=self.get_pct(
                        geo_code_statistics['met_{}'.format(stat)],
                        geo_code_statistics[stat]
                    ),
                    decimal_places=self.decimal_places
                )

            self.statistics[geo_code]={
                'id': geo_code,
                'statistics':geo_code_statistics
            }

    def serialize(self, minify=False):
        return self.statistics

class Terminal(Utils):
    def __init__(self, terminal_group_obj, id, terminal_data, decimal_places=3, bay_kwargs={}, gate_kwargs={}, **kwargs):
        self.terminal_group_obj=terminal_group_obj
        self.id=id
        self.terminal_data=terminal_data
        self.bay_kwargs=bay_kwargs
        self.gate_kwargs=gate_kwargs

        self.decimal_places=3

        self.gates=[]
        self.bays=[]

        self.populate_data()

    def populate_data(self):
        self.num_bays=self.terminal_data.get('num_bays', 1)+max(self.bay_kwargs.get('extra_bays',0),0)
        fuel_types=self.terminal_data.get('fuel_types',[])
        max_bay_queue=self.bay_kwargs.get('max_bay_queue')
        max_gate_queue=self.gate_kwargs.get('max_gate_queue')
        shared_queue=Queue(max_queue_length=max_bay_queue)
        for i in range(0, self.num_bays):
            if self.bay_kwargs.get('share_bay_queue_bool',False):
                queue=shared_queue
            else:
                queue=Queue(max_queue_length=max_bay_queue)
            bay=Bay(
                id='{id}_bay_{num}'.format(id=self.id, num=i),
                queue=queue,
                **{**{'fuel_types':fuel_types},**self.bay_kwargs}
            )
            self.bays.append(bay)
            self.terminal_group_obj.bays.append(bay)
            self.terminal_group_obj.all_objects.append(bay)

        self.num_gates=self.terminal_data.get('num_gates',1)+max(self.gate_kwargs.get('extra_gates',0),0)
        shared_queue=Queue(max_queue_length=max_gate_queue)
        for i in range(0, self.num_gates):
            if self.gate_kwargs.get('share_gate_queue_bool',False):
                queue=shared_queue
            else:
                queue=Queue(max_queue_length=max_gate_queue)
            gate=Gate(
                id='{id}_gate_{num}'.format(id=self.id, num=i),
                bays=self.bays,
                queue=queue,
                **self.gate_kwargs
            )
            self.gates.append(gate)
            self.terminal_group_obj.gates.append(gate)
            self.terminal_group_obj.all_objects.append(gate)

    def get_statistics(self):
        self.statistics={}

        bay_stats=['fuel_supplied', 'trucks_serviced']
        for stat in bay_stats:
            self.statistics["sum_{}".format(stat)]=sum([x.statistics[stat] for x in self.bays])

        iter_gate_stats=['in_gate_queue','wait_for_gate_to_open','gate_processing']
        for stat in iter_gate_stats:
            self.statistics['trip_sum_{}'.format(stat)]=self.round_sum([x.statistics['iter_events'].get(stat,{}).get('total',0) for x in self.gates])
            self.statistics['trip_cnt_{}'.format(stat)]=self.round_sum([x.statistics['iter_events'].get(stat,{}).get('count',0) for x in self.gates])
            self.statistics['trip_avg_{}'.format(stat)]=self.hardRound(a=self.safeDivide(
                self.statistics['trip_cnt_{}'.format(stat)],
                self.statistics['trip_sum_{}'.format(stat)]
            ),decimal_places=self.decimal_places)

        iter_bay_stats=['in_bay_queue', 'wait_for_bay_to_open', 'wait_for_bay_queue_space', 'bay_fill_truck']
        for stat in iter_bay_stats:
            self.statistics['trip_sum_{}'.format(stat)]=self.round_sum([x.statistics['iter_events'].get(stat,{}).get('total',0) for x in self.bays])
            self.statistics['trip_cnt_{}'.format(stat)]=self.round_sum([x.statistics['iter_events'].get(stat,{}).get('count',0) for x in self.bays])
            self.statistics['trip_avg_{}'.format(stat)]=self.hardRound(a=self.safeDivide(
                self.statistics['trip_cnt_{}'.format(stat)],
                self.statistics['trip_sum_{}'.format(stat)]
            ),decimal_places=self.decimal_places)


        gate_stats=['gate_processing']
        for stat in gate_stats:
            self.statistics['gate_sum_{}'.format(stat)]=self.round_sum([x.statistics['iter_events'].get(stat,{}).get('total',0) for x in self.gates])
            self.statistics['gate_cnt_{}'.format(stat)]=len(self.gates)
            self.statistics['gate_avg_{}'.format(stat)]=self.hardRound(a=self.safeDivide(
                self.statistics['gate_cnt_{}'.format(stat)],
                self.statistics['gate_sum_{}'.format(stat)]
            ),decimal_places=self.decimal_places)

        bay_stats=['bay_fill_truck']
        for stat in bay_stats:
            self.statistics['bay_sum_{}'.format(stat)]=self.round_sum([x.statistics['iter_events'].get(stat,{}).get('total',0) for x in self.bays])
            self.statistics['bay_cnt_{}'.format(stat)]=len(self.bays)
            self.statistics['bay_avg_{}'.format(stat)]=self.hardRound(a=self.safeDivide(
                self.statistics['bay_cnt_{}'.format(stat)],
                self.statistics['bay_sum_{}'.format(stat)]
            ),decimal_places=self.decimal_places)

        self.statistics['cnt_bays']=self.num_bays
        self.statistics['cnt_gates']=self.num_gates

        return self.statistics

    def serialize(self, minify=False):
        if minify:
            statistics={k:v for k,v in self.statistics.items() if '_sum_' not in k and '_cnt_' not in k}
        else:
            statistics=self.statistics
        return {
            'id': self.id,
            'latitude':self.terminal_data.get('latitude',0),
            'longitude':self.terminal_data.get('longitude',0),
            'name':self.terminal_data.get('name', 'Name Not Found'),
            'statistics':statistics
            }

class Terminal_Group(Utils):
    def __init__(self, terminal_group_data, id, geo_codes, demand_situation, demand_multiplier=1, truck_multiplier=1, station_algorithm='cycle_demand_hi_low', initial_distance_to_terminal_group=0, initial_distance_to_terminal_group_sigma=0, default_travel_distance=30, decimal_places=3, carry_over_demand={}, truck_kwargs={}, station_kwargs={}, default_terminal_kwargs={}, terminal_kwargs={}, **kwargs):
        # Terminal System Level kwargs
        self.geo_codes=geo_codes
        self.carry_over_demand=carry_over_demand

        # Terminal Group Level Kwargs
        self.id=id
        self.terminal_group_data=terminal_group_data
        self.demand_situation=demand_situation
        self.demand_multiplier=demand_multiplier
        self.truck_multiplier=truck_multiplier
        self.station_algorithm=station_algorithm
        self.truck_kwargs=truck_kwargs
        self.station_kwargs=station_kwargs
        self.initial_distance_to_terminal_group=initial_distance_to_terminal_group
        self.initial_distance_to_terminal_group_sigma=initial_distance_to_terminal_group_sigma

        self.decimal_places=3
        self.default_travel_distance=default_travel_distance

        # Terminal Level Kwargs
        self.default_terminal_kwargs=default_terminal_kwargs
        self.terminal_kwargs=terminal_kwargs

        # Irrelevant Kwargs For Posterity
        self.kwargs=kwargs

        # Storage of created objects for simulation
        self.stations=[]
        self.trucks=[]
        self.gates=[]
        self.bays=[]

        # Storage of terminal objects for statistics
        self.terminals=[]
        self.all_objects=[]

        # Flag Storage
        self.station_flags=[]

        self.populate_data()
        Simulate(self.all_objects)
        self.get_statistics()
        self.get_next_carry_over_demand()

    def populate_data(self):
        for terminal_id, terminal_data in self.terminal_group_data['terminals'].items():
            kwargs=self.mergeDeep(
                dict(self.terminal_kwargs.get(terminal_id,{})),
                dict(self.default_terminal_kwargs)
            )
            self.terminals.append(
                Terminal(
                    terminal_group_obj=self,
                    id=terminal_id,
                    terminal_data=terminal_data,
                    **kwargs)
            )

        for station_id, station_data in self.terminal_group_data['stations'].items():
            new_demand=int(station_data.get("demand_scenarios", {}).get(self.demand_situation, {}).get('demand',0)*self.demand_multiplier)

            if new_demand<0:
                new_demand=0
                carry_over_demand=0
                total_demand=0
            else:
                carry_over_demand=max(int(self.carry_over_demand.get(station_id,0)),0)
                total_demand=new_demand+carry_over_demand

            station=Station(
                id=station_id,
                data=station_data,
                name=station_data.get('name', 'No Name Found'),
                distance=station_data.get('travel_distance', self.default_travel_distance),
                new_demand=new_demand,
                carry_over_demand=carry_over_demand,
                demand=total_demand,
                geo_code=station_data.get('geo_code', 10000),
                fuel_types=station_data.get("demand_scenarios", {}).get(self.demand_situation, {}).get('fuel_types',[]),
                **self.station_kwargs
            )
            self.stations.append(station)
            self.all_objects.append(station)
            self.geo_codes.add_object(station)

            station_flag_data=station_data.get("demand_scenarios", {}).get(self.demand_situation, {}).get('flag',{})
            station_flag_data['id']=station_id
            for item in ['latitude', 'longitude', 'name']:
                station_flag_data[item]=station_data.get(item,0)
            station_flag=Flag(data=station_flag_data)
            self.station_flags.append(station_flag)

        for i in range(0, int(self.terminal_group_data['trucks_available']*self.truck_multiplier)):
            initial_distance_to_terminal_group=max(random.normalvariate(self.initial_distance_to_terminal_group,self.initial_distance_to_terminal_group_sigma),0)
            truck=Truck(
                id='Truck_{i}'.format(i=i),
                initial_distance_to_terminal_group=initial_distance_to_terminal_group,
                gates=self.gates,
                stations=self.stations,
                **self.truck_kwargs
            )
            self.trucks.append(truck)
            self.all_objects.append(truck)

    def serialize(self, minify=False):
        if minify:
            statistics={k:v for k,v in self.statistics.items() if '_sum_' not in k and '_cnt_' not in k}
        else:
            statistics=self.statistics
        return {
            'id':self.id,
            'name':self.terminal_group_data.get('name', 'Name Not Found'),
            'statistics':statistics,
            'terminals': {terminal.id:terminal.serialize(minify=minify) for terminal in self.terminals},
            'stations':{station.id:station.serialize(minify=minify) for station in self.stations},
            'station_flags':{flag.id:flag.serialize(minify=minify) for flag in self.station_flags}
        }

    def get_statistics(self):
        stat_data=[terminal.get_statistics() for terminal in self.terminals]

        self.statistics={}
        used_trucks=[x for x in self.trucks if x.completed_a_delivery]
        self.statistics['truck_cnt_available']=len(self.trucks)
        self.statistics['truck_cnt_used']=len(used_trucks)

        truck_stats = ['not_open','to_terminal_group','wait_for_gate_to_open','in_gate_queue','gate_processing','wait_for_bay_to_open','in_bay_queue','bay_fill_truck','to_station','wait_for_station_to_open','in_station_queue','station_empty_truck', 'close_early']
        for stat in truck_stats:
            data=[x.statistics['events'].get(stat,{}).get('total',0) for x in used_trucks]
            stat_sum=self.round_sum(data)
            stat_cnt=len(data)
            self.statistics['truck_sum_{stat}'.format(stat=stat)]=stat_sum
            self.statistics['truck_cnt_{stat}'.format(stat=stat)]=stat_cnt
            self.statistics['truck_avg_{stat}'.format(stat=stat)]=self.hardRound(a=self.safeDivide(stat_cnt, stat_sum),decimal_places=self.decimal_places)

        fuel_delivered_data=[x.statistics.get('fuel_delivered',0) for x in used_trucks]
        fuel_delivered=self.round_sum(fuel_delivered_data)
        fuel_deliveries=len(fuel_delivered_data)
        self.statistics['truck_sum_fuel_delivered']=fuel_delivered
        self.statistics['truck_cnt_fuel_delivered']=fuel_deliveries
        self.statistics['truck_avg_fuel_delivered']=self.hardRound(a=fuel_delivered/max(fuel_deliveries,1),decimal_places=self.decimal_places)

        def sum_item_from_station_data(item):
            return self.round_sum([i.statistics[item] for i in self.stations])

        def get_demand_pct_met(item):
            return self.hardRound(a=self.safeDivide(
                self.statistics['sum_{}'.format(item)],
                self.statistics['sum_met_{}'.format(item)]
            ),decimal_places=self.decimal_places)

        sum_stats=['total_demand','new_demand','carry_over_demand','met_total_demand','met_new_demand','met_carry_over_demand','unmet_total_demand']
        for stat in sum_stats:
            self.statistics['sum_{}'.format(stat)]=sum_item_from_station_data(stat)

        pct_stats=['total_demand','new_demand','carry_over_demand']
        for stat in pct_stats:
            self.statistics['pct_{}_met'.format(stat)]=get_demand_pct_met(stat)

        terminal_queue_stats = {'gate':['gate_processing'], 'bay':['bay_fill_truck'], 'trip':['in_gate_queue','wait_for_gate_to_open','gate_processing', 'in_bay_queue', 'wait_for_bay_to_open', 'wait_for_bay_queue_space', 'bay_fill_truck']}
        for queue_type, stats in terminal_queue_stats.items():
            for stat in stats:
                self.statistics['{qt}_sum_{st}'.format(qt=queue_type, st=stat)]=self.round_sum([x.get('{qt}_sum_{st}'.format(qt=queue_type, st=stat),0) for x in stat_data])
                self.statistics['{qt}_cnt_{st}'.format(qt=queue_type, st=stat)]=self.round_sum([x.get('{qt}_cnt_{st}'.format(qt=queue_type, st=stat),0) for x in stat_data])
                self.statistics['{qt}_avg_{st}'.format(qt=queue_type, st=stat)]=self.hardRound(a=self.safeDivide(
                    self.statistics['{qt}_cnt_{st}'.format(qt=queue_type, st=stat)],
                    self.statistics['{qt}_sum_{st}'.format(qt=queue_type, st=stat)]
                ),decimal_places=self.decimal_places)

        sum_stats = ['cnt_bays', 'cnt_gates', 'sum_trucks_serviced', 'sum_fuel_supplied']
        for stat in sum_stats:
            self.statistics['{}'.format(stat)]=self.round_sum([x.get('{}'.format(stat),0) for x in stat_data])

        iter_station_stats=['to_station','in_station_queue','station_empty_truck']
        for stat in iter_station_stats:
            self.statistics['trip_sum_{}'.format(stat)]=self.round_sum([x.statistics['iter_events'].get(stat,{}).get('total',0) for x in self.stations])
            self.statistics['trip_cnt_{}'.format(stat)]=self.round_sum([x.statistics['iter_events'].get(stat,{}).get('count',0) for x in self.stations])
            self.statistics['trip_avg_{}'.format(stat)]=self.hardRound(a=self.safeDivide(
                self.statistics['trip_cnt_{}'.format(stat)],
                self.statistics['trip_sum_{}'.format(stat)]
            ),decimal_places=self.decimal_places)

        self.statistics['truck_avg_deliveries_made']=self.hardRound(a=self.safeDivide(
            self.statistics['truck_cnt_used'],
            self.statistics['trip_cnt_station_empty_truck']
        ),decimal_places=self.decimal_places)

    def get_next_carry_over_demand(self):
        self.next_carry_over_demand={x.id:x.demand for x in self.stations}

class Terminal_System(Utils):
    def __init__(self, terminal_system_data, geo_areas, decimal_places=3, carry_over_demand_multiplier=0, terminal_kwargs={}, terminal_group_kwargs={}, default_kwargs={}, carry_over_demand={}, **kwargs):
        self.terminal_system_data=terminal_system_data
        self.geo_areas=geo_areas
        self.terminal_groups={}
        self.terminal_group_kwargs=terminal_group_kwargs
        self.terminal_kwargs=terminal_kwargs
        self.default_kwargs=default_kwargs
        self.carry_over_demand={key:value*carry_over_demand_multiplier for key, value in carry_over_demand.items()}
        self.next_carry_over_demand={}
        self.decimal_places=decimal_places
        self.kwargs=kwargs
        self.geo_codes=Geo_Codes(geo_areas)
        self.populate_data()
        self.get_statistics()
        self.get_geocode_statistics()
        self.get_next_carry_over_demand()

    def get_geocode_statistics(self):
        self.geo_codes.get_statistics()

    def populate_data(self):
        for terminal_group_id, terminal_group_data in self.terminal_system_data.items():
            kwargs=self.mergeDeep(
                dict(self.terminal_group_kwargs.get(terminal_group_id,{})),
                dict(self.default_kwargs)
            )
            default_terminal_kwargs=kwargs.pop('terminal_kwargs',{})
            self.terminal_groups[terminal_group_id]=Terminal_Group(
                terminal_group_data=terminal_group_data,
                id=terminal_group_id,
                geo_codes=self.geo_codes,
                terminal_kwargs=self.terminal_kwargs,
                default_terminal_kwargs=default_terminal_kwargs,
                carry_over_demand=self.carry_over_demand,
                **kwargs)

    def get_statistics(self):
        stat_data=[x.statistics for x in self.terminal_groups.values()]

        self.statistics={}

        def sum_item_from_stat_data(item):
            return self.round_sum(self.pluck(path=[item], data=stat_data))

        self.statistics['truck_cnt_available']=sum_item_from_stat_data('truck_cnt_available')
        self.statistics['truck_cnt_used']=sum_item_from_stat_data('truck_cnt_used')

        truck_stats = ['not_open','to_terminal_group','wait_for_gate_to_open','in_gate_queue','gate_processing','wait_for_bay_to_open','in_bay_queue','bay_fill_truck','to_station','wait_for_station_to_open','in_station_queue','station_empty_truck', 'close_early', 'fuel_delivered']
        for stat in truck_stats:
            stat_sum=sum_item_from_stat_data('truck_sum_{stat}'.format(stat=stat))
            stat_cnt=sum_item_from_stat_data('truck_cnt_{stat}'.format(stat=stat))
            self.statistics['truck_sum_{stat}'.format(stat=stat)]=stat_sum
            self.statistics['truck_cnt_{stat}'.format(stat=stat)]=stat_cnt
            self.statistics['truck_avg_{stat}'.format(stat=stat)]=self.hardRound(a=self.safeDivide(stat_cnt, stat_sum), decimal_places=self.decimal_places)

        def get_demand_pct_met(item):
            return self.hardRound(a=self.safeDivide(
                self.statistics['sum_{}'.format(item)],
                self.statistics['sum_met_{}'.format(item)]
            ),decimal_places=self.decimal_places)

        terminal_queue_stats = {'gate':['gate_processing'], 'bay':['bay_fill_truck'], 'trip':['in_gate_queue', 'wait_for_gate_to_open', 'gate_processing', 'in_bay_queue', 'wait_for_bay_to_open', 'wait_for_bay_queue_space', 'bay_fill_truck', 'to_station', 'in_station_queue', 'station_empty_truck']}
        for queue_type, stats in terminal_queue_stats.items():
            for stat in stats:
                self.statistics['{qt}_sum_{st}'.format(qt=queue_type, st=stat)]=self.round_sum([x.get('{qt}_sum_{st}'.format(qt=queue_type, st=stat),0) for x in stat_data])
                self.statistics['{qt}_cnt_{st}'.format(qt=queue_type, st=stat)]=self.round_sum([x.get('{qt}_cnt_{st}'.format(qt=queue_type, st=stat),0) for x in stat_data])
                self.statistics['{qt}_avg_{st}'.format(qt=queue_type, st=stat)]=self.hardRound(a=self.safeDivide(
                    self.statistics['{qt}_cnt_{st}'.format(qt=queue_type, st=stat)],
                    self.statistics['{qt}_sum_{st}'.format(qt=queue_type, st=stat)]
                ),decimal_places=self.decimal_places)

        sum_stats = ['cnt_bays', 'cnt_gates', 'sum_trucks_serviced', 'sum_fuel_supplied', 'sum_total_demand', 'sum_new_demand', 'sum_carry_over_demand', 'sum_met_total_demand', 'sum_met_new_demand', 'sum_met_carry_over_demand', 'sum_unmet_total_demand']
        for stat in sum_stats:
            self.statistics['{}'.format(stat)]=sum_item_from_stat_data(stat)

        pct_stats=['total_demand','new_demand','carry_over_demand']
        for stat in pct_stats:
            self.statistics['pct_{}_met'.format(stat)]=get_demand_pct_met(stat)

        self.statistics['truck_avg_deliveries_made']=self.hardRound(a=self.safeDivide(
            self.statistics['truck_cnt_used'],
            self.statistics['trip_cnt_station_empty_truck']
        ),decimal_places=self.decimal_places)

    def serialize(self, minify=False):
        if minify:
            statistics={k:v for k,v in self.statistics.items() if '_sum_' not in k and '_cnt_' not in k}
        else:
            statistics=self.statistics
        return {
            'statistics':statistics,
            'terminal_groups': {id:data.serialize(minify=minify) for id, data in self.terminal_groups.items()},
            'geo_codes':self.geo_codes.serialize(minify=minify)
        }

    def get_next_carry_over_demand(self):
        for terminal_group in self.terminal_groups.values():
            self.next_carry_over_demand.update(terminal_group.next_carry_over_demand)

class Multi_Period_Terminal_System(Utils):
    def __init__(self, terminal_system_data, geo_areas, periods, **kwargs):
        self.terminal_system_data=terminal_system_data
        self.geo_areas=geo_areas
        self.periods=periods
        self.terminal_systems={}
        self.kwargs=kwargs
        self.populate_data()

    def populate_data(self):
        next_carry_over_demand={}
        for i in range(0,len(self.periods)):
            period=self.periods[i]
            terminal_system=Terminal_System(
                terminal_system_data=self.terminal_system_data,
                geo_areas=self.geo_areas,
                carry_over_demand=next_carry_over_demand,
                **period
            )
            self.terminal_systems[str(i)]=terminal_system
            next_carry_over_demand=terminal_system.next_carry_over_demand

    def serialize(self, minify=False):
        return {key:value.serialize(minify) for key, value in self.terminal_systems.items()}
