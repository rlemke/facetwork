lazy val root = (project in file("."))
  .settings(
    name := "fw-agent",
    organization := "afl",
    version := "0.1.0",
    scalaVersion := "3.3.4",

    libraryDependencies ++= Seq(
      // MongoDB Scala driver (published for 2.13; use cross for Scala 3)
      ("org.mongodb.scala" %% "mongo-scala-driver" % "5.3.1").cross(CrossVersion.for3Use2_13),
      "ch.qos.logback" % "logback-classic" % "1.5.18",
      "org.scalatest" %% "scalatest" % "3.2.19" % Test
    ),

    // Scala 3 compiler options
    scalacOptions ++= Seq(
      "-deprecation",
      "-feature",
      "-unchecked"
    )
  )
