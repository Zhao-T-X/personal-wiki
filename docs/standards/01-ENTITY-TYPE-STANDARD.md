# Entity Type Standard v1.0

## 1. Definition

An Entity is a knowledge object with an independent identity, stable semantic boundary, reusable identity and meaningful long-term knowledge value.

A noun, keyword, adjective, ordinary action, attribute, sentence, or transient phrase is not automatically an Entity.

## 2. Type registry

The v1.0 registry contains exactly 14 types:

```text
Person
Organization
Product
Software
Technology
Method
Concept
Theory
Dataset
Model
Standard
Protocol
Resource
Location
```

These types are controlled vocabulary. The LLM may not invent new types.

## 3. Type definitions

### Person
A naturally identifiable person with independent identity.

### Organization
A stable organization, institution, company, team or other organizational entity that is independently identifiable in context.

### Product
A product offered, distributed or provided as an identifiable product.

### Software
An executable or deployable software system, program, library, framework or software tool.

### Technology
A technically meaningful technology, architecture, technical system, capability or technology family that can be independently discussed.

### Method
A method, algorithm, procedure, workflow or way of achieving a goal.

### Concept
An abstract object that can be independently defined, discussed, compared or used as a durable knowledge topic.

### Theory
A relatively coherent explanatory theory or theoretical framework.

### Dataset
An independently identifiable organized collection of data. Benchmark datasets may be represented as Dataset.

### Model
An independently identifiable model used for training, inference, evaluation or deployment.

### Standard
A formally standardized or normative specification maintained by a recognized body, community or process.

### Protocol
A defined protocol for communication, interaction, coordination or interoperability.

### Resource
A separately identifiable knowledge or technical resource that cannot reasonably be classified as one of the other 13 types.

`Resource` is not an uncertainty bucket.

### Location
A separately identifiable geographic, spatial or site/location entity.

## 4. Type boundaries

### Technology versus Method

Technology answers **what technical capability/system is this?**

Method answers **how is a task performed?**

An Entity may have both types when the source independently supports both. Example: a source may describe RAG as both a technology and a method.

### Technology versus Concept

A Technology has technical implementation or system meaning. A Concept is abstract and does not inherently represent an implementation.

### Model versus Technology

A Model is an identifiable model artifact or model family. It may also be classified as Technology when the source independently treats it as a technology object.

### Standard versus Protocol

A Protocol defines interaction/communication behavior. A Standard is a formalized normative specification. An object may have both types when supported.

### Resource

Use Resource only after the other types have been considered. If the type is too uncertain to establish responsibly, omit the Entity instead of defaulting to Resource.

## 5. Entity inclusion test

Before extraction, ask:

1. Does the object have independent identity?
2. Can it be referred to outside this sentence without losing its identity?
3. Does it have a stable semantic boundary?
4. Does it have reusable knowledge value?
5. Is it more than a fact, property, relation, action or generic phrase?

If the answers do not support Entity status, do not extract it.

## 6. Canonical naming and aliases

The canonical name must be concise, stable and reusable. Do not put descriptions into the name.

Aliases must actually occur in the source or be explicitly established by the source. Do not invent synonyms.

## 7. Multiple types

Multiple types are allowed:

```json
{
  "name": "RAG",
  "types": ["Technology", "Method"]
}
```

Every additional type requires source support. Do not add types merely because the model knows they are plausible.

## 8. Type uncertainty

When type confidence is insufficient, omit the Entity. Do not invent a new type and do not use Resource as a fallback solely because classification is uncertain.
