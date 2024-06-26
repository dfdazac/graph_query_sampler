"""Import a dataset that was created in the format as for the KGReasoning framework

    This focuses on getting three parts:
    1. the ID mapping
    2. the data splits
    3. the test queries.

    It can also convert the train and validation queries. However, often it is up to any system to create them the way they want, potentially using the gqs framework.

    This import assumes that the original relation mapping has alternating forward and backward relations in the imported mapping.
    The forward relations are kept with their original indices.
    The backward relations retain their indices, but are never used.
    If backward relations are used in the queries, then the triple containing that relation is inverted and the forward relation type is used.

"""


import json
import logging
import pickle
import pathlib
from typing import Any, Callable, Literal, Type, TypeVar, cast
from gqs.conversion import protobuf_builder
from gqs.dataset import Dataset
from gqs.mapping import RelationMapper, EntityMapper
from gqs.conversion import QueryBuilder
from gqs.query_representation.query_pb2 import Query, QueryData

logger = logging.getLogger(__name__)


def KGReasoning_to_zero_qual_queries_dataset(import_source: pathlib.Path, dataset: Dataset, lenient: bool, splits: list[Literal["train", "test", "validation"]] | None = None) -> None:
    splits = splits or ["train", "test", "validation"]
    dataset.location().mkdir(parents=True)
    dataset.mapping_location().mkdir()
    _convert_mapper(import_source / "id2ent.pkl", dataset.entity_mapping_location())
    _convert_mapper(import_source / "id2rel.pkl", dataset.relation_mapping_location())
    dataset.splits_location().mkdir(parents=True)
    _convert_graph_splits(import_source, dataset)
    dataset.query_proto_location().mkdir(parents=True)
    for split in splits:
        if split == "train":
            _convert_queries(import_source, dataset, lenient, split, add_answers_train(import_source, dataset))
        else:
            _convert_queries(import_source, dataset, lenient, split, add_answers_test_validation(import_source, dataset, split))


def _convert_mapper(id2X_file: pathlib.Path, target_file: pathlib.Path) -> None:
    with open(id2X_file, "rb") as f:
        mapping = pickle.load(f)
    # sanity checks
    num_ids = len(mapping)
    for i in range(num_ids):
        assert i in mapping, f"The id {i} was not found in the mapping file {id2X_file}. Cannot convert"
    with open(target_file, "w") as output:
        sep = ""
        for i in range(num_ids):
            output.write(f"{sep}{mapping[i]}")
            sep = "\n"


def _convert_graph_splits(import_source: pathlib.Path, dataset: Dataset) -> None:
    ent_map: EntityMapper = dataset.entity_mapper
    rel_map: RelationMapper = dataset.relation_mapper

    for source_name, target_name in [("test.txt", "test.nt"), ("valid.txt", "validation.nt"), ("train.txt", "train.nt")]:
        with open(import_source / source_name) as input_file:
            with open(dataset.splits_location() / target_name, "w") as output_file:
                for line in input_file:
                    parts = line.split()
                    assert len(parts) == 3, f"splitting the line {line} in {source_name} did not split in 3 parts"
                    try:
                        s_int = int(parts[0])
                        p_int = int(parts[1])
                        o_int = int(parts[2])
                    except ValueError as e:
                        raise Exception("for the line {line} in {source_name}, one of the parts could not be parsed as an int") from e
                    s = ent_map.inverse_lookup(s_int)
                    p = rel_map.inverse_lookup(p_int)
                    o = ent_map.inverse_lookup(o_int)
                    line = f"<{s}> <{p}> <{o}> .\n"
                    output_file.write(line)


T = TypeVar('T')

KGQueryShape = tuple[Any, ...]
KGQueryInstance = tuple[Any, ...]


def _mappers(rel_map: RelationMapper, ent_map: EntityMapper, builder_factory: Type[QueryBuilder[T]]) -> dict[KGQueryShape, tuple[str, Callable[[KGQueryInstance], QueryBuilder[T]]]]:
    # In all of these, we map from int to str. Most builders will map this back to int. this could be optimized out.

    def set_triple(builder: QueryBuilder[T], triple_index: int, subject: str, predicate_ID: int, object: str) -> None:
        if predicate_ID % 2 == 0:
            # predicate is a forward relation
            builder.set_subject_predicate_entity_object(triple_index, subject, rel_map.inverse_lookup(predicate_ID), object)
        else:
            # predicate is an backward relation, first get the forward relation ID
            forward_predicate_ID_int = predicate_ID - 1
            del predicate_ID

            predicate_ID_str = rel_map.inverse_lookup(forward_predicate_ID_int)
            # Add the inverted edge
            builder.set_subject_predicate_entity_object(triple_index, object, predicate_ID_str, subject)

    def _1hop(KGlib_query: KGQueryInstance) -> QueryBuilder[T]:
        builder = builder_factory(1, 0)
        builder.set_diameter(1)
        (e, (r,)) = KGlib_query
        # most builders will map this back..

        set_triple(builder,
                   0,
                   ent_map.inverse_lookup(e),
                   r,
                   EntityMapper.get_target_entity_name()
                   )

        return builder

    def _2hop(KGlib_query: KGQueryInstance) -> QueryBuilder[T]:
        builder = builder_factory(2, 0)
        builder.set_diameter(2)
        (e, (r0, r1)) = KGlib_query
        # most builders will map this back..
        set_triple(builder,
                   0,
                   ent_map.inverse_lookup(e),
                   r0,
                   "?var0")
        set_triple(builder,
                   1,
                   "?var0",
                   r1,
                   EntityMapper.get_target_entity_name())
        return builder

    def _3hop(KGlib_query: KGQueryInstance) -> QueryBuilder[T]:
        builder = builder_factory(3, 0)
        builder.set_diameter(3)
        e, (r0, r1, r2) = KGlib_query
        # most builders will map this back..
        set_triple(builder,
                   0,
                   ent_map.inverse_lookup(e),
                   r0,
                   "?var0")
        set_triple(builder,
                   1,
                   "?var0",
                   r1,
                   "?var1")
        set_triple(builder,
                   2,
                   "?var1",
                   r2,
                   EntityMapper.get_target_entity_name())
        return builder

    def _2i(KGlib_query: KGQueryInstance) -> QueryBuilder[T]:
        builder = builder_factory(2, 0)
        builder.set_diameter(1)
        ((e0, (r0,)), (e1, (r1,))) = KGlib_query
        # most builders will map this back..
        set_triple(builder,
                   0,
                   ent_map.inverse_lookup(e0),
                   r0,
                   EntityMapper.get_target_entity_name())
        set_triple(builder,
                   1,
                   ent_map.inverse_lookup(e1),
                   r1,
                   EntityMapper.get_target_entity_name())
        return builder

    def _3i(KGlib_query: KGQueryInstance) -> QueryBuilder[T]:
        builder = builder_factory(3, 0)
        builder.set_diameter(1)
        ((e0, (r0,)), (e1, (r1,)), (e2, (r2,))) = KGlib_query
        # most builders will map this back..
        set_triple(builder,
                   0,
                   ent_map.inverse_lookup(e0),
                   r0,
                   EntityMapper.get_target_entity_name())
        set_triple(builder,
                   1,
                   ent_map.inverse_lookup(e1),
                   r1,
                   EntityMapper.get_target_entity_name())
        set_triple(builder,
                   2,
                   ent_map.inverse_lookup(e2),
                   r2,
                   EntityMapper.get_target_entity_name())

        return builder

    def _2i_1hop(KGlib_query: KGQueryInstance) -> QueryBuilder[T]:
        builder = builder_factory(3, 0)
        builder.set_diameter(2)
        ((e0, (r0,)), (e1, (r1,))), (r2,) = KGlib_query
        # most builders will map this back..
        set_triple(builder,
                   0,
                   ent_map.inverse_lookup(e0),
                   r0,
                   "?var0")
        set_triple(builder,
                   1,
                   ent_map.inverse_lookup(e1),
                   r1,
                   "?var0")
        set_triple(builder,
                   2,
                   "?var0",
                   r2,
                   EntityMapper.get_target_entity_name())
        return builder

    def _1hop_2i(KGlib_query: KGQueryInstance) -> QueryBuilder[T]:
        builder = builder_factory(3, 0)
        builder.set_diameter(2)
        ((e0, (r0, r1)), (e1, (r2,))) = KGlib_query
        # most builders will map this back..
        set_triple(builder,
                   0,
                   ent_map.inverse_lookup(e0),
                   r0,
                   "?var0")
        set_triple(builder,
                   1,
                   "?var0",
                   r1,
                   EntityMapper.get_target_entity_name())
        set_triple(builder,
                   2,
                   ent_map.inverse_lookup(e1),
                   r2,
                   EntityMapper.get_target_entity_name())

        return builder

    mapping: dict[KGQueryShape, tuple[str, Callable[[KGQueryInstance], QueryBuilder[T]]]] = {
        ('e', ('r',)): ("1hop", _1hop),
        ('e', ('r', 'r')): ("2hop", _2hop),
        ('e', ('r', 'r', 'r')): ("3hop", _3hop),
        (('e', ('r',)), ('e', ('r',))): ("2i", _2i),
        (('e', ('r',)), ('e', ('r',)), ('e', ('r',))): ("3i", _3i),
        ((('e', ('r',)), ('e', ('r',))), ('r',)): ('2i-1hop', _2i_1hop),
        (('e', ('r', 'r')), ('e', ('r',))): ('1hop-2i', _1hop_2i),
    }
    return mapping


Answer_Adder_Type = Callable[[QueryBuilder[Query], KGQueryInstance], None]


def add_answers_test_validation(import_source: pathlib.Path, dataset: Dataset, split: Literal["test"] | Literal["validation"]) -> Answer_Adder_Type:
    if split == "validation":
        kg_reasoning_split = "valid"
    else:
        kg_reasoning_split = "test"
    with open(import_source / f"{kg_reasoning_split}-easy-answers.pkl", "rb") as f:
        all_easy_answers = pickle.load(f)
    with open(import_source / f"{kg_reasoning_split}-hard-answers.pkl", "rb") as f:
        all_hard_answers = pickle.load(f)

    def answer_adder(builder: QueryBuilder[Query], query: KGQueryInstance) -> None:
        easy_answers = all_easy_answers[query]
        builder.set_easy_entity_targets([dataset.entity_mapper.inverse_lookup(t) for t in easy_answers])
        hard_answers = all_hard_answers[query]
        builder.set_hard_entity_targets([dataset.entity_mapper.inverse_lookup(t) for t in hard_answers])
    return answer_adder


def add_answers_train(import_source: pathlib.Path, dataset: Dataset) -> Answer_Adder_Type:
    """Add answers for a training set. This assumes the file "train-answers.pkl"

    Args:
        import_source (pathlib.Path): The import directory
        dataset (Dataset): dataset where the import will be written to, used for mapping indices

    Returns:
        Answer_Adder_Type: A function to be used for the converter
    """
    with open(import_source / "train-answers.pkl", "rb") as f:
        all_answers = pickle.load(f)

    def answer_adder(builder: QueryBuilder[Query], query: KGQueryInstance) -> None:
        answers = all_answers[query]
        builder.set_easy_entity_targets([dataset.entity_mapper.inverse_lookup(t) for t in answers])
    return answer_adder


def _convert_queries(import_source: pathlib.Path, dataset: Dataset, lenient: bool, split: str, answer_adder: Answer_Adder_Type) -> None:
    """Convert the queries to the gqs format

    Args:
        import_source (pathlib.Path): the folder containing the queries to be imported
        dataset (Dataset): the target dataset
        lenient (bool): if false, raises an exception if an unknown query shape is encountered, otherwise logs a warning
        answer_adder: the function used to add the answers. This is different for train vs validation and test. One could also implement something which gets answers from a database
    Raises:
        Exception: raises if an unknown query shape is encountered and lenient is false
    """
    builder_factory = protobuf_builder(dataset.relation_mapper, dataset.entity_mapper)
    mappers = _mappers(dataset.relation_mapper, dataset.entity_mapper, builder_factory)
    if split == "validation":
        kg_reasoning_split = "valid"
    else:
        kg_reasoning_split = split
    with open(import_source / f"{kg_reasoning_split}-queries.pkl", "rb") as f:
        queries = pickle.load(f)

    for query_shape, query_instances in queries.items():
        query_shape = cast(KGQueryShape, query_shape)
        query_instances = cast(list[KGQueryInstance], query_instances)
        if query_shape not in mappers:
            if lenient:
                logger.warning(f"The shape {query_shape} was not found. Likely not yet implemented")
                continue
            else:
                raise Exception(f"The shape {query_shape} was not found. Likely not yet implemented")
        query_shape_name, mapper = mappers[query_shape]
        proto_query_data = QueryData()
        for query in query_instances:
            builder = mapper(query)
            answer_adder(builder, query)
            proto_query: Query = builder.build()
            proto_query_data.queries.append(proto_query)

        output_folder = dataset.query_proto_location() / query_shape_name / "0qual"
        output_folder.mkdir(parents=True, exist_ok=True)
        output_file_name = output_folder / f"{split}.proto"
        with open(output_file_name, "wb") as output_file:
            output_file.write(proto_query_data.SerializeToString())
        # We also need a stats file for this, creating that here
        stats_file_name = output_folder / f"{split}_stats.json"
        stats = {"name": split, "count": len(query_instances), "hash": f"converted_from_{import_source}_{query_shape}"}
        try:
            with stats_file_name.open(mode="w") as stats_file:
                json.dump(stats, stats_file)
        except Exception:
            # something went wrong writing the stats file, best to remove it and crash.
            logger.error("Failed writing the stats, removing the file to avoid inconsistent state")
            if stats_file_name.exists():
                stats_file_name.unlink()
            raise
        print(f"Done with shape {query_shape}")
