"""
nskernel.contracts · 领域必须实现的 6 大契约

用法:
  from nskernel.contracts import (
      DomainProfile, PersonaProfile, IdentityProfile,
      EntityScheme, EntityRef, DetectionResult,
      ObservationSchema, Observation,
      AuthoritySchema, AuthorityRef, RetrievalQuery, RetrievalResult,
      ComputationSpec, FormulaDef, UnitSpec, CalculationRequest, CalculationResult,
      DecisionSchema, Decision, DecisionItem,
  )
"""
from .domain_profile import DomainProfile, PersonaProfile, IdentityProfile
from .entity_scheme import EntityScheme, EntityRef, DetectionResult
from .observation_schema import ObservationSchema, Observation
from .authority_schema import (
    AuthoritySchema, AuthorityRef, RetrievalQuery, RetrievalResult,
)
from .computation_spec import (
    ComputationSpec, FormulaDef, UnitSpec,
    CalculationRequest, CalculationResult,
)
from .decision_schema import DecisionSchema, Decision, DecisionItem

__all__ = [
    # Profile
    "DomainProfile", "PersonaProfile", "IdentityProfile",
    # Entity
    "EntityScheme", "EntityRef", "DetectionResult",
    # Observation
    "ObservationSchema", "Observation",
    # Authority
    "AuthoritySchema", "AuthorityRef", "RetrievalQuery", "RetrievalResult",
    # Computation
    "ComputationSpec", "FormulaDef", "UnitSpec",
    "CalculationRequest", "CalculationResult",
    # Decision
    "DecisionSchema", "Decision", "DecisionItem",
    # 🆕 v0.5: 3 个高级契约(借鉴道路医生)
    "EnvironmentMatrixSchema", "EnvironmentProfile",
    "SensitivityCoefficient", "TreatmentWindow",
    "AmplificationResult", "RiskAssessment",
    "CausalChainSchema", "CausalChain", "ChainCondition",
    "ChainMatch", "CausalAnalysisResult",
    "AgentSignalProtocol", "CoordinationSignal", "CoordinationResult",
]


# 🆕 v0.5
from .environment_matrix_schema import (
    EnvironmentMatrixSchema, EnvironmentProfile,
    SensitivityCoefficient, TreatmentWindow,
    AmplificationResult, RiskAssessment,
)
from .causal_chain_schema import (
    CausalChainSchema, CausalChain, ChainCondition,
    ChainMatch, CausalAnalysisResult,
)
from .agent_signal_protocol import (
    AgentSignalProtocol, CoordinationSignal, CoordinationResult,
)
